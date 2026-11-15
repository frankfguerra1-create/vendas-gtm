#!/usr/bin/env python3
"""Coletor do Briefing GTM.

Principios (auditoria):
  1. Nenhum valor e inventado: ou vem de API oficial, ou de entrada manual
     validada (data/entrada_manual.json), ou o indicador fica "sem_dado".
  2. Cada valor guarda fonte, URL, data de referencia, instante da coleta,
     metodo e SHA-256 da resposta bruta.
  3. Falha de uma fonte nunca derruba as outras; o status vai para o JSON.
  4. Somente biblioteca padrao do Python.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BRT = timezone(timedelta(hours=-3))  # Brasil sem horario de verao desde 2019
UA = "briefing-gtm-coletor/1.0"
OLINDA = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{}/dados"


# ----------------------------------------------------------------- rede
def http_get(url: str, tentativas: int = 3, timeout: int = 30) -> bytes:
    ultimo = None
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ultimo = e
            time.sleep(2 ** n)
    raise ConnectionError(f"{type(ultimo).__name__}: {ultimo}")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------- BACEN
def _num(x) -> float:
    return float(str(x).replace(",", "."))


def _br(d: date) -> str:  # dd/MM/yyyy (SGS)
    return d.strftime("%d/%m/%Y")


def _us(d: date) -> str:  # MM-dd-yyyy (Olinda)
    return d.strftime("%m-%d-%Y")


def parse_ptax(raw: bytes) -> list[dict]:
    """Retorna [{data: date, valor: float}] ordenado, 1 por dia (boletim de
    fechamento quando o campo tipoBoletim existir; senao o ultimo do dia)."""
    obj = json.loads(raw)
    if not isinstance(obj, dict) or "value" not in obj:
        raise ValueError(f"esquema inesperado (chaves: {list(obj)[:5] if isinstance(obj, dict) else type(obj)})")
    por_dia: dict[date, tuple[str, float]] = {}
    for r in obj["value"]:
        if "cotacaoVenda" not in r or "dataHoraCotacao" not in r:
            raise ValueError(f"registro sem campos esperados: {sorted(r)}")
        if r.get("tipoBoletim") not in (None, "Fechamento", "Fechamento PTAX"):
            continue
        dia = datetime.fromisoformat(r["dataHoraCotacao"].replace(" ", "T")[:19]).date()
        por_dia[dia] = (r["dataHoraCotacao"], _num(r["cotacaoVenda"]))
    return [{"data": d, "valor": v[1]} for d, v in sorted(por_dia.items())]


def parse_sgs(raw: bytes) -> list[dict]:
    arr = json.loads(raw)
    if not isinstance(arr, list):
        raise ValueError(f"esquema inesperado: {str(arr)[:120]}")
    out = []
    for r in arr:
        if "data" not in r or "valor" not in r:
            raise ValueError(f"registro sem campos esperados: {sorted(r)}")
        out.append({"data": datetime.strptime(r["data"], "%d/%m/%Y").date(), "valor": _num(r["valor"])})
    return sorted(out, key=lambda x: x["data"])


def url_ptax(ind_id: str, hoje: date) -> str:
    ini, fim = hoje - timedelta(days=45), hoje
    if ind_id == "usd_ptax":
        base = (OLINDA + "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
                f"?@dataInicial='{_us(ini)}'&@dataFinalCotacao='{_us(fim)}'")
    else:
        base = (OLINDA + "CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
                f"?@moeda='EUR'&@dataInicial='{_us(ini)}'&@dataFinalCotacao='{_us(fim)}'")
    return base + "&$top=500&$format=json"


def url_sgs(codigo: int, hoje: date) -> str:
    dias = 400 if codigo in (433, 13522) else 60
    q = urllib.parse.urlencode({"formato": "json", "dataInicial": _br(hoje - timedelta(days=dias)), "dataFinal": _br(hoje)})
    return SGS.format(codigo) + "?" + q


# ------------------------------------------------------------ validacao
def avaliar(ind: dict, serie: list[dict], hoje: date) -> dict:
    """Aplica regras de auditoria a uma serie ja lida. Pura (testavel)."""
    if not serie:
        return {"status": "sem_dado", "motivo": "fonte respondeu sem registros"}
    ult = serie[-1]
    r = {"valor": round(ult["valor"], ind.get("casas", 4)), "data_referencia": ult["data"].isoformat()}
    avisos = []
    idade = (hoje - ult["data"]).days
    r["idade_dias"] = idade
    if ult["data"] > hoje:
        return {"status": "invalido", "motivo": "data de referencia no futuro"}
    if not (ind["min"] <= ult["valor"] <= ind["max"]):
        return {"status": "invalido", "motivo": f"valor {ult['valor']} fora do intervalo de sanidade [{ind['min']}, {ind['max']}]"}
    if len(serie) >= 2:
        ant = serie[-2]
        r["valor_anterior"] = round(ant["valor"], ind.get("casas", 4))
        r["data_anterior"] = ant["data"].isoformat()
        if ant["valor"]:
            r["variacao_pct"] = round((ult["valor"] / ant["valor"] - 1) * 100, 2)
    r["historico"] = [{"d": s["data"].isoformat(), "v": round(s["valor"], ind.get("casas", 4))} for s in serie[-30:]]
    if idade > ind["max_idade_dias"]:
        r["status"] = "desatualizado"
        avisos.append(f"dado com {idade} dias (limite {ind['max_idade_dias']})")
    else:
        r["status"] = "ok"
    r["avisos"] = avisos
    return r


def coletar_auto(ind: dict, hoje: date) -> dict:
    url = url_ptax(ind["id"], hoje) if ind["id"] in ("usd_ptax", "eur_ptax") else url_sgs(ind["sgs"], hoje)
    base = {"metodo": "automatico", "endpoint": url, "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        raw = http_get(url)
        base["sha256_resposta"] = sha(raw)
        serie = parse_ptax(raw) if ind["id"] in ("usd_ptax", "eur_ptax") else parse_sgs(raw)
        base.update(avaliar(ind, serie, hoje))
    except Exception as e:  # noqa: BLE001 - qualquer falha vira status auditavel
        base.update({"status": "erro_coleta", "motivo": f"{type(e).__name__}: {e}"})
    return base


# --------------------------------------------------------------- manual
CAMPOS_MANUAIS = ("id", "valor", "unidade", "indicador_oficial", "data_referencia", "fonte_url", "inserido_por", "inserido_em")


def validar_entrada_manual(e: dict, cfg: dict, hoje: date, hist_anterior: dict | None) -> dict:
    faltando = [c for c in CAMPOS_MANUAIS if e.get(c) in (None, "")]
    if faltando:
        return {"status": "invalido", "motivo": f"campos obrigatorios ausentes: {faltando}"}
    try:
        valor = float(e["valor"])
        dref = date.fromisoformat(e["data_referencia"])
    except (TypeError, ValueError) as ex:
        return {"status": "invalido", "motivo": f"formato invalido: {ex}"}
    if valor <= 0:
        return {"status": "invalido", "motivo": "valor deve ser positivo"}
    if dref > hoje:
        return {"status": "invalido", "motivo": "data de referencia no futuro"}
    if not str(e["fonte_url"]).startswith("https://"):
        return {"status": "invalido", "motivo": "fonte_url deve ser https://"}
    idade = (hoje - dref).days
    r = {"valor": valor, "unidade": e["unidade"], "indicador_oficial": e["indicador_oficial"],
         "data_referencia": dref.isoformat(), "idade_dias": idade, "fonte_url_entrada": e["fonte_url"],
         "inserido_por": e["inserido_por"], "inserido_em": e["inserido_em"], "avisos": []}
    if hist_anterior and hist_anterior["data_referencia"] < dref.isoformat() and hist_anterior["valor"]:
        v = (valor / hist_anterior["valor"] - 1) * 100
        r["valor_anterior"], r["data_anterior"], r["variacao_pct"] = hist_anterior["valor"], hist_anterior["data_referencia"], round(v, 2)
        if abs(v) > cfg["alerta_variacao_manual_pct"]:
            r["avisos"].append(f"variacao de {v:+.1f}% vs entrada anterior: conferir digitacao e unidade")
    if idade > cfg["max_idade_manual_dias"]:
        r["status"] = "desatualizado"
        r["avisos"].append(f"dado com {idade} dias (limite {cfg['max_idade_manual_dias']})")
    else:
        r["status"] = "ok"
    return r


def ler_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ----------------------------------------------------------------- main
def montar(hoje: date, agora_utc: datetime) -> dict:
    cfg = json.loads((RAIZ / "config/fontes.json").read_text(encoding="utf-8"))
    manual_p = RAIZ / "data/entrada_manual.json"
    entradas = {}
    if manual_p.exists():
        for e in json.loads(manual_p.read_text(encoding="utf-8")).get("entradas", []):
            ant = entradas.get(e.get("id"))
            if ant is None or str(e.get("data_referencia")) >= str(ant.get("data_referencia")):
                entradas[e.get("id")] = e
    hist_p = RAIZ / "data/historico_manual.jsonl"
    hist = ler_jsonl(hist_p)
    novos_hist = []
    saida, avisos = [], []

    for ind in cfg["indicadores"]:
        item = {k: ind[k] for k in ("id", "nome", "grupo", "modo", "fonte", "url") if k in ind}
        if "nota" in ind:
            item["nota"] = ind["nota"]
        if ind["modo"] == "auto":
            item["unidade"] = ind["unidade"]
            item.update(coletar_auto(ind, hoje))
        else:
            e = entradas.get(ind["id"])
            if not e:
                item.update({"metodo": "manual", "status": "sem_dado",
                             "motivo": "nenhuma entrada manual registrada em data/entrada_manual.json"})
            else:
                anteriores = sorted((h for h in hist if h["id"] == ind["id"] and h["data_referencia"] < str(e.get("data_referencia"))),
                                    key=lambda h: h["data_referencia"])
                item["metodo"] = "manual"
                item.update(validar_entrada_manual(e, cfg, hoje, anteriores[-1] if anteriores else None))
                if item["status"] in ("ok", "desatualizado") and not any(
                        h["id"] == ind["id"] and h["data_referencia"] == e["data_referencia"] for h in hist):
                    novos_hist.append({"id": ind["id"], "valor": float(e["valor"]), "data_referencia": e["data_referencia"],
                                       "inserido_por": e["inserido_por"], "registrado_em": agora_utc.isoformat(timespec="seconds")})
        if item["status"] not in ("ok",):
            avisos.append(f"{ind['id']}: {item['status']}" + (f" — {item.get('motivo')}" if item.get("motivo") else ""))
        for a in item.get("avisos", []):
            avisos.append(f"{ind['id']}: {a}")
        saida.append(item)

    if novos_hist:
        with hist_p.open("a", encoding="utf-8") as f:
            for h in novos_hist:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")

    resumo = {s: sum(1 for i in saida if i["status"] == s) for s in sorted({i["status"] for i in saida})}
    return {
        "versao_esquema": 1,
        "data_briefing": hoje.isoformat(),
        "gerado_em_utc": agora_utc.isoformat(timespec="seconds"),
        "indicadores": saida,
        "resumo_status": resumo,
        "avisos": avisos,
    }


def main() -> int:
    agora = datetime.now(timezone.utc)
    hoje = agora.astimezone(BRT).date()
    out = montar(hoje, agora)
    (RAIZ / "data").mkdir(exist_ok=True)
    (RAIZ / "data/briefing.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # trilha de auditoria: 1 linha por execucao (sem historico de precos, so metadados)
    (RAIZ / "data/auditoria").mkdir(parents=True, exist_ok=True)
    with (RAIZ / "data/auditoria/execucoes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"data_briefing": out["data_briefing"], "gerado_em_utc": out["gerado_em_utc"],
                            "resumo_status": out["resumo_status"],
                            "hashes": {i["id"]: i.get("sha256_resposta") for i in out["indicadores"] if i.get("sha256_resposta")}},
                           ensure_ascii=False) + "\n")
    print(json.dumps(out["resumo_status"], ensure_ascii=False))
    for a in out["avisos"]:
        print("AVISO:", a)
    # falha dura so se NENHUMA fonte automatica funcionou (evita publicar painel vazio sem alarme)
    autos = [i for i in out["indicadores"] if i["modo"] == "auto"]
    if autos and all(i["status"] in ("erro_coleta", "sem_dado", "invalido") for i in autos):
        print("ERRO: nenhuma fonte automatica respondeu com dado valido", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
