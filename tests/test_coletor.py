"""Testes das regras de auditoria. Usam respostas SIMULADAS (fixtures de formato),
nao dados de mercado: verificam logica, nao precos. Rodar: python3 -m unittest -v"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import coletar_dados as c  # noqa: E402

HOJE = date(2026, 11, 16)
IND = {"id": "x", "min": 3, "max": 10, "casas": 4, "max_idade_dias": 5}
CFG = {"max_idade_manual_dias": 7, "alerta_variacao_manual_pct": 15}


def ptax(*regs):
    return json.dumps({"value": [{"cotacaoVenda": v, "dataHoraCotacao": f"{d} 13:00:00.000", **({"tipoBoletim": t} if t else {})}
                                 for d, v, t in regs]}).encode()


class Ptax(unittest.TestCase):
    def test_pega_fechamento_e_ignora_abertura(self):
        s = c.parse_ptax(ptax(("2026-11-12", 1.0, "Abertura"), ("2026-11-12", 2.0, "Fechamento")))
        self.assertEqual(s, [{"data": date(2026, 11, 12), "valor": 2.0}])

    def test_esquema_inesperado_levanta(self):
        with self.assertRaises(ValueError):
            c.parse_ptax(b'{"erro":"x"}')
        with self.assertRaises(ValueError):
            c.parse_ptax(b'{"value":[{"foo":1}]}')

    def test_sgs_virgula_e_ordem(self):
        s = c.parse_sgs(b'[{"data":"02/01/2026","valor":"5,5"},{"data":"01/01/2026","valor":"5.4"}]')
        self.assertEqual([x["valor"] for x in s], [5.4, 5.5])

    def test_sgs_esquema_inesperado(self):
        with self.assertRaises(ValueError):
            c.parse_sgs(b'{"error":"limite"}')


class Avaliar(unittest.TestCase):
    def serie(self, *pares):
        return [{"data": d, "valor": v} for d, v in pares]

    def test_ok_com_variacao(self):
        r = c.avaliar(IND, self.serie((date(2026, 11, 12), 5.0), (date(2026, 11, 13), 5.5)), HOJE)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["variacao_pct"], 10.0)
        self.assertEqual(r["valor_anterior"], 5.0)

    def test_desatualizado(self):
        r = c.avaliar(IND, self.serie((date(2026, 11, 1), 5.0)), HOJE)
        self.assertEqual(r["status"], "desatualizado")

    def test_fora_da_faixa_vira_invalido_e_nao_exibe_valor(self):
        r = c.avaliar(IND, self.serie((date(2026, 11, 13), 55.0)), HOJE)
        self.assertEqual(r["status"], "invalido")
        self.assertNotIn("valor", r)

    def test_futuro_invalido(self):
        self.assertEqual(c.avaliar(IND, self.serie((date(2026, 12, 1), 5.0)), HOJE)["status"], "invalido")

    def test_vazio_sem_dado(self):
        self.assertEqual(c.avaliar(IND, [], HOJE)["status"], "sem_dado")


class Manual(unittest.TestCase):
    base = {"id": "boi_gordo", "valor": 100.0, "unidade": "u", "indicador_oficial": "n", "data_referencia": "2026-11-13",
            "fonte_url": "https://exemplo.org/x", "inserido_por": "t", "inserido_em": "2026-11-13T08:00:00-03:00"}

    def test_ok(self):
        self.assertEqual(c.validar_entrada_manual(self.base, CFG, HOJE, None)["status"], "ok")

    def test_campo_ausente(self):
        e = dict(self.base); del e["inserido_por"]
        self.assertEqual(c.validar_entrada_manual(e, CFG, HOJE, None)["status"], "invalido")

    def test_http_rejeitado_e_valor_nao_positivo(self):
        self.assertEqual(c.validar_entrada_manual({**self.base, "fonte_url": "http://x"}, CFG, HOJE, None)["status"], "invalido")
        self.assertEqual(c.validar_entrada_manual({**self.base, "valor": 0}, CFG, HOJE, None)["status"], "invalido")

    def test_salto_grande_gera_aviso(self):
        r = c.validar_entrada_manual(self.base, CFG, HOJE, {"valor": 50.0, "data_referencia": "2026-11-06"})
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("conferir" in a for a in r["avisos"]))

    def test_antigo_desatualizado(self):
        self.assertEqual(c.validar_entrada_manual({**self.base, "data_referencia": "2026-10-01"}, CFG, HOJE, None)["status"], "desatualizado")


class Montagem(unittest.TestCase):
    def test_sem_rede_nada_e_inventado(self):
        orig = c.http_get
        c.http_get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("sem rede"))
        try:
            from datetime import datetime, timezone
            out = c.montar(HOJE, datetime(2026, 11, 16, 10, tzinfo=timezone.utc))
        finally:
            c.http_get = orig
        for i in out["indicadores"]:
            self.assertNotIn("valor", i, i["id"])
            self.assertIn(i["status"], ("erro_coleta", "sem_dado"))


if __name__ == "__main__":
    unittest.main()
