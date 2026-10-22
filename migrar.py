#!/usr/bin/env python
"""Migra cadastro, imoveis e fotos de banco_nexus para novon14.

Cadeia de dois remapeamentos de ID:

    tcadastro      CadCodigo 57 -> 912          (lastrowid)
    timoveis       ImoCadCodigo = 912
                   ImoCodigo 4312 -> 901877     (lastrowid)
    tfotosimoveis  FotImoCodigo = 901877

So o codigo de origem e informado. O do destino nasce do proprio INSERT.

    python migrar.py --cad-origem 57 --dry-run
    python migrar.py --cad-origem 57 --limite 20
    python migrar.py --cad-origem 57
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

from migracao import ORIGEM_ROTULO, cadastro, config, controle, esquema, validacao
from migracao.db import conectar, um_valor
from migracao.migrador import Migrador

RAIZ = Path(__file__).resolve().parent


class Opcoes:
    def __init__(self, args, execucao):
        self.cad_origem = args.cad_origem
        self.cad_destino = None  # definido depois de migrar o cadastro
        self.dry_run = args.dry_run
        self.limite = args.limite
        self.desde = args.desde
        self.bloco = args.bloco
        self.checar_ref = not args.sem_checar_ref
        self.carimbar = args.carimbar_migracao
        self.parar_no_erro = args.parar_no_erro
        self.chave_natural = args.chave_natural
        self.limpar_conexao_banco = args.limpar_conexao_banco
        self.execucao = execucao
        self.rotulo_origem = ORIGEM_ROTULO


def montar_argumentos():
    p = argparse.ArgumentParser(
        description="Migracao banco_nexus -> novon14: cadastro + imoveis + fotos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--cad-origem", type=int, required=True,
                   help="CadCodigo do cliente NA ORIGEM. O codigo do destino e gerado.")
    p.add_argument("--dry-run", action="store_true",
                   help="Ensaia os imoveis com ROLLBACK. O cadastro NAO e inserido "
                        "(tcadastro e MyISAM, nao daria para desfazer).")
    p.add_argument("--limite", type=int, default=None,
                   help="Migra no maximo N imoveis. Use para amostra.")
    p.add_argument("--desde", type=int, default=None,
                   help="Comeca a partir deste ImoCodigo da origem.")
    p.add_argument("--bloco", type=int, default=200,
                   help="Imoveis lidos da origem por vez (default 200).")
    p.add_argument("--chave-natural", default=None,
                   choices=cadastro.CHAVES_NATURAIS,
                   help="Coluna usada para reconhecer o cliente no destino. "
                        f"Default: a primeira com valor entre {', '.join(cadastro.CHAVES_NATURAIS)}.")
    p.add_argument("--limpar-conexao-banco", action="store_true",
                   help="Grava vazio em CadIpBanco/Usuario/Senha/Base em vez de copiar "
                        "as credenciais do banco do cliente da origem.")
    p.add_argument("--so-cadastro", action="store_true",
                   help="Migra apenas o cadastro e para. Util para conferir antes dos imoveis.")
    p.add_argument("--sem-checar-ref", action="store_true",
                   help="Nao conferir ImoRef duplicada no destino antes de inserir.")
    p.add_argument("--carimbar-migracao", action="store_true",
                   help="Grava 'nexus:<ImoCodigo>' em ImoCodigoMigracao. "
                        "ATENCAO: sobrescreve o valor da origem.")
    p.add_argument("--parar-no-erro", action="store_true",
                   help="Interrompe no primeiro imovel que falhar.")
    p.add_argument("--ignorar-diferenca-esquema", action="store_true",
                   help="Segue mesmo com divergencia entre origem e destino.")
    p.add_argument("--aceitar-sem-transacao", action="store_true",
                   help="PERIGOSO: segue mesmo que timoveis/tfotosimoveis nao sejam "
                        "InnoDB no destino. Sem InnoDB nao existe ROLLBACK e a garantia "
                        "de 'imovel e fotos juntos' deixa de valer.")
    p.add_argument("--env", default=None, help="Caminho alternativo do .env.")
    return p


def main():
    args = montar_argumentos().parse_args()
    execucao = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    config.carregar_env(args.env)
    par_origem = config.parametros("ORIGEM")
    par_destino = config.parametros("DESTINO")

    if (par_origem["host"], par_origem["port"], par_origem["database"]) == (
        par_destino["host"], par_destino["port"], par_destino["database"]
    ):
        raise SystemExit("Origem e destino apontam para a mesma base. Abortado.")

    (RAIZ / "logs").mkdir(exist_ok=True)
    arquivo_log = RAIZ / "logs" / f"migracao-{execucao}.log"
    handle = arquivo_log.open("w", encoding="utf-8")

    def log(msg):
        linha = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(linha, flush=True)
        handle.write(linha + "\n")
        handle.flush()

    log("=" * 72)
    log(f"execucao {execucao}" + ("  [DRY-RUN]" if args.dry_run else ""))
    log(f"origem  {par_origem['user']}@{par_origem['host']}/{par_origem['database']}")
    log(f"destino {par_destino['user']}@{par_destino['host']}/{par_destino['database']}")
    log(f"cliente de origem: CadCodigo {args.cad_origem}")
    log("=" * 72)

    origem = conectar(par_origem, autocommit=True, rotulo="origem")
    destino = conectar(par_destino, autocommit=False, rotulo="destino")

    try:
        log(f"sql_mode no destino: {um_valor(destino, 'SELECT @@session.sql_mode')}")

        # --- engines: de onde vem (ou nao) a atomicidade -------------------
        log("-" * 72)
        for nome in ("tcadastro", "timoveis", "tfotosimoveis"):
            eo = esquema.ler_engine(origem, par_origem["database"], nome)
            ed = esquema.ler_engine(destino, par_destino["database"], nome)
            log(f"engine {nome:<14} origem={eo:<8} destino={ed}")

        sem_transacao = esquema.exigir_transacional(
            destino, par_destino["database"], ("timoveis", "tfotosimoveis")
        )
        if sem_transacao:
            detalhe = ", ".join(f"{n} ({e})" for n, e in sem_transacao)
            log(f"ATENCAO: no destino, {detalhe} nao e InnoDB.")
            log("  Sem InnoDB o ROLLBACK e ignorado: um imovel pode ficar gravado")
            log("  sem as fotos, e o mapa pode divergir do que esta no banco.")
            if not args.aceitar_sem_transacao:
                raise SystemExit(
                    "Abortado: a garantia de atomicidade nao existe nessas tabelas. "
                    "Converta para InnoDB (ALTER TABLE ... ENGINE=InnoDB) ou, "
                    "assumindo o risco, repita com --aceitar-sem-transacao."
                )
            log("  seguindo por --aceitar-sem-transacao; risco aceito explicitamente")

        # tcadastro ser MyISAM e esperado, nao e erro -- so precisa ser dito.
        if esquema.ler_engine(destino, par_destino["database"], "tcadastro") != "INNODB":
            log("tcadastro nao e InnoDB (esperado): o INSERT do cadastro nao pode")
            log("  ser desfeito. A protecao contra duplicata e a chave natural.")

        # --- estrutura -----------------------------------------------------
        log("-" * 72)
        tabelas = {}
        for chave, nome in (("cadastro", "tcadastro"), ("imoveis", "timoveis"),
                            ("fotos", "tfotosimoveis")):
            tabelas[f"{chave}_origem"] = esquema.ler_tabela(
                origem, par_origem["database"], nome)
            tabelas[f"{chave}_destino"] = esquema.ler_tabela(
                destino, par_destino["database"], nome)

        divergencias = []
        for chave in ("cadastro", "imoveis", "fotos"):
            divergencias += esquema.comparar(
                tabelas[f"{chave}_origem"], tabelas[f"{chave}_destino"])
        if divergencias:
            log(f"ATENCAO: {len(divergencias)} divergencia(s) de estrutura:")
            for d in divergencias:
                log(f"  - {d}")
            if not args.ignorar_diferenca_esquema:
                raise SystemExit(
                    "Abortado por divergencia de estrutura. Revise acima e, se for "
                    "aceitavel, repita com --ignorar-diferenca-esquema."
                )
            log("seguindo por --ignorar-diferenca-esquema; colunas sem par ignoradas")
        else:
            log("estruturas das tres tabelas conferem entre origem e destino")

        # --- controle ------------------------------------------------------
        controle.criar(destino)
        opcoes = Opcoes(args, execucao)

        # --- 1. cadastro ---------------------------------------------------
        log("-" * 72)
        log("ETAPA 1/2 -- cadastro da imobiliaria")
        resultado_cad = cadastro.migrar(origem, destino, tabelas, opcoes, log)
        opcoes.cad_destino = resultado_cad.cad_destino
        log(f"ImoCadCodigo que sera gravado nos imoveis: {opcoes.cad_destino}"
            + ("  (ficticio, dry-run)" if resultado_cad.motivo == "dry_run" else ""))

        if args.so_cadastro:
            log("--so-cadastro: parando aqui.")
            return 0

        # --- 2. imoveis + fotos --------------------------------------------
        log("-" * 72)
        log("ETAPA 2/2 -- imoveis e fotos")
        total_origem = um_valor(
            origem, "SELECT COUNT(*) FROM `timoveis` WHERE ImoCadCodigo = %s",
            (args.cad_origem,),
        )
        if not total_origem:
            log(f"Nenhum imovel na origem com ImoCadCodigo = {args.cad_origem}.")
            log("Cadastro migrado, mas nao havia imovel para trazer.")
            return 0

        feitos = controle.ja_migrados(destino, ORIGEM_ROTULO, args.cad_origem)
        migrador = Migrador(origem, destino, tabelas, opcoes)
        resultado = migrador.executar(feitos, log)

        # --- fechamento ----------------------------------------------------
        log("-" * 72)
        log(str(resultado))
        if args.dry_run:
            log("DRY-RUN: transacoes de imoveis sofreram ROLLBACK; cadastro nao foi tocado.")

        if resultado.erros:
            log(f"primeiros erros ({min(10, len(resultado.erros))} de {len(resultado.erros)}):")
            for erro in resultado.erros[:10]:
                log(f"  ! {erro}")

        ocorrencias = controle.resumo_ocorrencias(destino, execucao)
        if ocorrencias:
            log("ocorrencias em _mig_ocorrencia:")
            for linha in ocorrencias:
                log(f"  {linha['tipo']}: {linha['total']}")

        if not args.dry_run and resultado.migrados:
            log("-" * 72)
            log("validacao:")
            for checagem, total in validacao.rodar(destino, ORIGEM_ROTULO):
                log(f"  [{'OK  ' if total == 0 else 'FALHA'}] {checagem['id']} "
                    f"{checagem['titulo']}: {total}")
            for chave, valor in validacao.contagem_fim_a_fim(
                origem, destino, ORIGEM_ROTULO, args.cad_origem
            ).items():
                log(f"  {chave}: {valor}")

        log(f"log salvo em {arquivo_log}")
        return 1 if resultado.falhas else 0

    finally:
        handle.close()
        origem.close()
        destino.close()


if __name__ == "__main__":
    sys.exit(main())
