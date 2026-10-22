"""Leitura do .env. Sem dependencia externa: o unico pacote do projeto e o PyMySQL."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def carregar_env(caminho=None):
    arquivo = Path(caminho) if caminho else RAIZ / ".env"
    if not arquivo.exists():
        raise SystemExit(
            f"Configuracao nao encontrada: {arquivo}\n"
            "Copie .env.example para .env e preencha as credenciais."
        )
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ[chave.strip()] = valor.strip().strip('"').strip("'")


def parametros(prefixo):
    """prefixo: 'ORIGEM' ou 'DESTINO'."""
    obrigatorios = ("HOST", "USER", "DATABASE")
    faltando = [f"{prefixo}_{c}" for c in obrigatorios if not os.environ.get(f"{prefixo}_{c}")]
    if faltando:
        raise SystemExit("Variaveis obrigatorias ausentes no .env: " + ", ".join(faltando))
    return {
        "host": os.environ[f"{prefixo}_HOST"],
        "port": int(os.environ.get(f"{prefixo}_PORT") or 3306),
        "user": os.environ[f"{prefixo}_USER"],
        "password": os.environ.get(f"{prefixo}_PASSWORD", ""),
        "database": os.environ[f"{prefixo}_DATABASE"],
    }
