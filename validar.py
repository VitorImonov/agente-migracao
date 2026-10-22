#!/usr/bin/env python
"""Validacao independente, rodavel a qualquer momento depois da migracao.

    python validar.py --cad-origem 57

Sai com codigo 1 se qualquer checagem critica falhar -- serve de porteiro
antes do corte.
"""

import argparse

from migracao import ORIGEM_ROTULO, config, validacao
from migracao.db import conectar, consultar


def main():
    p = argparse.ArgumentParser(description="Valida a coerencia imovel <-> foto no destino.")
    p.add_argument("--cad-origem", type=int, required=True,
                   help="ImoCadCodigo do cliente NA ORIGEM, para a contagem fim a fim.")
    p.add_argument("--env", default=None)
    args = p.parse_args()

    config.carregar_env(args.env)
    origem = conectar(config.parametros("ORIGEM"), autocommit=True, rotulo="origem")
    destino = conectar(config.parametros("DESTINO"), autocommit=True, rotulo="destino")

    try:
        print("=" * 72)
        print(f"VALIDACAO -- origem '{ORIGEM_ROTULO}' -- ImoCadCodigo origem {args.cad_origem}")
        print("=" * 72)

        falhas_criticas = 0
        print("\nChecagens de coerencia (todas devem dar 0)")
        print("-" * 72)
        for checagem, total in validacao.rodar(destino, ORIGEM_ROTULO):
            ok = total == 0
            if not ok and checagem["gravidade"] == "critica":
                falhas_criticas += 1
            print(f"[{'OK   ' if ok else 'FALHA'}] {checagem['id']} "
                  f"({checagem['gravidade']:<8}) {checagem['titulo']}: {total}")

        print("\nContagem fim a fim")
        print("-" * 72)
        contagens = validacao.contagem_fim_a_fim(
            origem, destino, ORIGEM_ROTULO, args.cad_origem
        )
        for chave, valor in contagens.items():
            print(f"{chave:<28} {valor}")

        falta_imovel = contagens["imoveis_origem"] - contagens["imoveis_migrados"]
        falta_foto = contagens["fotos_origem_vinculadas"] - contagens["fotos_migradas"]
        if falta_imovel or falta_foto:
            print(f"\nNAO migrados: {falta_imovel} imoveis, {falta_foto} fotos.")
            print("Veja o motivo em _mig_ocorrencia (ref_duplicada, falha, etc).")

        ocorrencias = consultar(
            destino,
            "SELECT tipo, COUNT(*) AS total FROM `_mig_ocorrencia` "
            "GROUP BY tipo ORDER BY total DESC",
        )
        if ocorrencias:
            print("\nOcorrencias acumuladas")
            print("-" * 72)
            for linha in ocorrencias:
                print(f"{linha['tipo']:<28} {linha['total']}")

        print("\n" + "=" * 72)
        if falhas_criticas:
            print(f"REPROVADO: {falhas_criticas} checagem(ns) critica(s) falhou. NAO faca o corte.")
            return 1
        print("APROVADO: relacao imovel <-> foto coerente.")
        return 0

    finally:
        origem.close()
        destino.close()


if __name__ == "__main__":
    raise SystemExit(main())
