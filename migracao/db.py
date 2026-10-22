"""Conexoes MySQL.

Duas decisoes deste modulo merecem explicacao, porque sao o que faz a migracao
sobreviver aos dados legados da origem:

1. DATAS COMO STRING
   A origem tem cinco colunas `date NOT NULL` sem default (ImoAngariacao,
   ImoFimExibicao, ImoDtInsert, ImoDtEnviaOlx, ImoDtAtualizaOlx), o que quase
   garante '0000-00-00' nos dados. O conversor padrao do PyMySQL devolve None
   para essa data, e None num `date NOT NULL` do destino quebra o INSERT.
   Mantendo o valor como string, '0000-00-00' entra no destino exatamente como
   estava na origem. Copia fiel, sem inventar data substituta.

2. sql_mode DA SESSAO
   Para o item 1 funcionar a sessao nao pode estar em modo estrito. Mas desligar
   o modo estrito tambem desliga o erro de truncamento, que passaria a ser
   silencioso. Por isso o truncamento e conferido em Python antes do INSERT
   (ver migrador.verificar_tamanhos) em vez de ser delegado ao banco.
"""

import pymysql
import pymysql.cursors
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions

# Datas e horas chegam cruas, como vieram do servidor.
CONVERSORES = conversions.copy()
for _nome in ("DATE", "DATETIME", "TIMESTAMP", "NEWDATE"):
    _tipo = getattr(FIELD_TYPE, _nome, None)
    if _tipo is not None:
        CONVERSORES[_tipo] = lambda valor: valor

# ALLOW_INVALID_DATES sem STRICT_TRANS_TABLES e sem NO_ZERO_DATE:
# aceita '0000-00-00' e datas como '2020-02-31' que existem na base antiga.
SQL_MODE_MIGRACAO = "ALLOW_INVALID_DATES"


def conectar(params, autocommit, rotulo=""):
    try:
        return pymysql.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=params["database"],
            charset="utf8mb4",
            conv=CONVERSORES,
            autocommit=autocommit,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=20,
            read_timeout=900,
            write_timeout=900,
            init_command=f"SET SESSION sql_mode='{SQL_MODE_MIGRACAO}'",
        )
    except pymysql.MySQLError as erro:
        alvo = f"{params['user']}@{params['host']}:{params['port']}/{params['database']}"
        raise SystemExit(f"Falha ao conectar em {rotulo or 'MySQL'} ({alvo}): {erro}")


def um_valor(conexao, sql, args=None):
    with conexao.cursor() as cur:
        cur.execute(sql, args)
        linha = cur.fetchone()
    if not linha:
        return None
    return next(iter(linha.values()))


def consultar(conexao, sql, args=None):
    with conexao.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()
