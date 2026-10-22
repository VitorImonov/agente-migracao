"""Migracao de imoveis e fotos, uma transacao por imovel.

A regra que este modulo existe para garantir:

    Nenhuma foto e inserida com o FotImoCodigo lido da origem.
    O valor vem sempre do lastrowid do INSERT do imovel, na mesma transacao.

Consequencia: e impossivel uma foto apontar para imovel inexistente ou, pior,
para o imovel de outro cliente que por acaso tenha o ID antigo no destino.
"""

import time

import pymysql

from . import controle
from .controle import (
    OC_DATA_ZERO,
    OC_DESTAQUE_NAO_RESOLVIDO,
    OC_FALHA,
    OC_REF_DUPLICADA,
    OC_SEM_FOTOS,
    OC_TRUNCAMENTO,
)

TIPOS_DATA = {"date", "datetime", "timestamp"}
DATA_ZERO = {"0000-00-00", "0000-00-00 00:00:00"}


class Resultado:
    def __init__(self):
        self.migrados = 0
        self.pulados_mapa = 0
        self.pulados_ref = 0
        self.falhas = 0
        self.fotos = 0
        self.fotos_orfas_ignoradas = 0
        self.erros = []

    def __str__(self):
        return (
            f"imoveis migrados={self.migrados} fotos={self.fotos} "
            f"pulados(ja no mapa)={self.pulados_mapa} "
            f"pulados(ImoRef duplicada)={self.pulados_ref} falhas={self.falhas}"
        )


class Migrador:
    def __init__(self, origem, destino, tabelas, opcoes):
        self.origem = origem
        self.destino = destino
        self.op = opcoes
        self.res = Resultado()

        ti_o, ti_d = tabelas["imoveis_origem"], tabelas["imoveis_destino"]
        tf_o, tf_d = tabelas["fotos_origem"], tabelas["fotos_destino"]

        # Apenas colunas presentes nas DUAS pontas. Se o destino tiver coluna a
        # mais, ela fica com o default dele em vez de estourar KeyError; se a
        # origem tiver a mais, o dado nao tem para onde ir -- e esquema.comparar()
        # ja reportou isso antes de chegar aqui.
        self.cols_imovel = [c for c in ti_d.colunas_para_inserir() if c in ti_o.por_nome]
        self.cols_foto = [c for c in tf_d.colunas_para_inserir() if c in tf_o.por_nome]

        # Limites de tamanho sao os do DESTINO: e nele que o truncamento ocorreria.
        self.limites = ti_d.limites_texto()
        self.limites_foto = tf_d.limites_texto()

        # Colunas de data da ORIGEM: e nelas que '0000-00-00' aparece.
        self.cols_data = [
            c["COLUMN_NAME"] for c in ti_o.colunas if c["DATA_TYPE"] in TIPOS_DATA
        ]

        self.sql_imovel = (
            f"INSERT INTO `timoveis` ("
            + ", ".join(f"`{c}`" for c in self.cols_imovel)
            + ") VALUES ("
            + ", ".join(["%s"] * len(self.cols_imovel))
            + ")"
        )
        self.sql_foto = (
            f"INSERT INTO `tfotosimoveis` ("
            + ", ".join(f"`{c}`" for c in self.cols_foto)
            + ") VALUES ("
            + ", ".join(["%s"] * len(self.cols_foto))
            + ")"
        )

    # ------------------------------------------------------------------ leitura

    def listar_codigos(self):
        """IDs do recorte na origem, em ordem estavel."""
        sql = "SELECT ImoCodigo FROM `timoveis` WHERE ImoCadCodigo = %s"
        args = [self.op.cad_origem]
        if self.op.desde:
            sql += " AND ImoCodigo >= %s"
            args.append(self.op.desde)
        sql += " ORDER BY ImoCodigo"
        if self.op.limite:
            sql += f" LIMIT {int(self.op.limite)}"
        with self.origem.cursor() as cur:
            cur.execute(sql, args)
            return [linha["ImoCodigo"] for linha in cur.fetchall()]

    def _buscar_imoveis(self, codigos):
        marcas = ", ".join(["%s"] * len(codigos))
        with self.origem.cursor() as cur:
            cur.execute(
                f"SELECT * FROM `timoveis` WHERE ImoCodigo IN ({marcas}) ORDER BY ImoCodigo",
                codigos,
            )
            return cur.fetchall()

    def _buscar_fotos(self, codigos):
        """Fotos do bloco em uma consulta, agrupadas por imovel.

        A ordem (FotPosicao, FotCodigo) e a ordem de insercao no destino, e e o
        que permite casar cada foto antiga com o FotCodigo novo depois.
        """
        marcas = ", ".join(["%s"] * len(codigos))
        with self.origem.cursor() as cur:
            cur.execute(
                f"SELECT * FROM `tfotosimoveis` WHERE FotImoCodigo IN ({marcas}) "
                "ORDER BY FotImoCodigo, FotPosicao, FotCodigo",
                codigos,
            )
            por_imovel = {}
            for foto in cur.fetchall():
                por_imovel.setdefault(foto["FotImoCodigo"], []).append(foto)
            return por_imovel

    # ------------------------------------------------------------- preparacao

    def _valores_imovel(self, imo, avisos):
        valores = []
        for coluna in self.cols_imovel:
            if coluna == "ImoCadCodigo":
                valor = self.op.cad_destino
            elif coluna == "ImoCodigoMigracao" and self.op.carimbar:
                valor = f"nexus:{imo['ImoCodigo']}"[:40]
            else:
                valor = imo[coluna]
            limite = self.limites.get(coluna)
            if limite and isinstance(valor, str) and len(valor) > limite:
                avisos.append((OC_TRUNCAMENTO, f"{coluna}: {len(valor)} > {limite}"))
            valores.append(valor)
        return valores

    def _valores_foto(self, foto, novo_imo, avisos):
        valores = []
        for coluna in self.cols_foto:
            # O unico campo que NAO vem da origem. E a razao de ser deste modulo.
            valor = novo_imo if coluna == "FotImoCodigo" else foto[coluna]
            limite = self.limites_foto.get(coluna)
            if limite and isinstance(valor, str) and len(valor) > limite:
                avisos.append(
                    (OC_TRUNCAMENTO, f"tfotosimoveis.{coluna} (FotCodigo {foto['FotCodigo']})")
                )
            valores.append(valor)
        return valores

    def _datas_zeradas(self, imo):
        return [c for c in self.cols_data if imo.get(c) in DATA_ZERO]

    # ------------------------------------------------------------- escrita

    def _migrar_um(self, imo, fotos):
        imo_origem = imo["ImoCodigo"]
        ref = (imo.get("ImoRef") or "").strip()
        avisos = []

        cur = self.destino.cursor()
        try:
            # ImoRef e unica dentro da mesma imobiliaria. Rede de seguranca
            # contra duplicata vinda de migracao anterior, fora deste mapa.
            if self.op.checar_ref and ref:
                cur.execute(
                    "SELECT ImoCodigo FROM `timoveis` WHERE ImoCadCodigo = %s AND ImoRef = %s LIMIT 1",
                    (self.op.cad_destino, ref),
                )
                existente = cur.fetchone()
                if existente:
                    controle.registrar_ocorrencia(
                        cur, self.op.execucao, imo_origem, OC_REF_DUPLICADA,
                        f"ImoRef '{ref}' ja existe no destino como ImoCodigo "
                        f"{existente['ImoCodigo']} para ImoCadCodigo {self.op.cad_destino}",
                    )
                    if self.op.dry_run:
                        self.destino.rollback()
                    else:
                        self.destino.commit()  # guarda a ocorrencia, nao o imovel
                    self.res.pulados_ref += 1
                    return

            # 1. imovel
            cur.execute(self.sql_imovel, self._valores_imovel(imo, avisos))
            novo_imo = cur.lastrowid

            # 2. mapa do imovel
            cur.execute(
                "INSERT INTO `_mig_mapa_imoveis` "
                "(origem, imo_cad_codigo, imo_cad_origem, imo_codigo_origem, "
                " imo_codigo_destino, imo_ref, qtd_fotos, execucao) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    self.op.rotulo_origem,
                    self.op.cad_destino,
                    self.op.cad_origem,
                    imo_origem,
                    novo_imo,
                    ref[:20],
                    len(fotos),
                    self.op.execucao,
                ),
            )

            # 3. fotos, sempre com o ID novo
            novos_codigos = []
            if fotos:
                cur.executemany(
                    self.sql_foto,
                    [self._valores_foto(f, novo_imo, avisos) for f in fotos],
                )
                # O imovel acabou de nascer no destino: tudo que tem este
                # FotImoCodigo foi inserido agora, nesta transacao. Lendo de
                # volta por FotCodigo (AUTO_INCREMENT = ordem de insercao)
                # casa cada foto antiga com a nova sem depender de IDs
                # contiguos nem de lastrowid de executemany.
                cur.execute(
                    "SELECT FotCodigo FROM `tfotosimoveis` WHERE FotImoCodigo = %s "
                    "ORDER BY FotCodigo",
                    (novo_imo,),
                )
                novos_codigos = [linha["FotCodigo"] for linha in cur.fetchall()]
                if len(novos_codigos) != len(fotos):
                    raise RuntimeError(
                        f"esperava {len(fotos)} fotos no destino, encontrei "
                        f"{len(novos_codigos)} para ImoCodigo {novo_imo}"
                    )
                cur.executemany(
                    "INSERT INTO `_mig_mapa_fotos` "
                    "(origem, fot_codigo_origem, fot_codigo_destino, imo_codigo_origem, execucao) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (self.op.rotulo_origem, antiga["FotCodigo"], novo, imo_origem,
                         self.op.execucao)
                        for antiga, novo in zip(fotos, novos_codigos)
                    ],
                )

            # 4. contadores desnormalizados: recalculados, nunca copiados
            self._atualizar_contadores(cur, imo, novo_imo, fotos, novos_codigos, avisos)

            # 5. ocorrencias do imovel, na mesma transacao
            datas_zero = self._datas_zeradas(imo)
            if datas_zero:
                controle.registrar_ocorrencia(
                    cur, self.op.execucao, imo_origem, OC_DATA_ZERO,
                    "0000-00-00 em: " + ", ".join(datas_zero),
                )
            for tipo, detalhe in avisos:
                controle.registrar_ocorrencia(cur, self.op.execucao, imo_origem, tipo, detalhe)

            if self.op.dry_run:
                self.destino.rollback()
            else:
                self.destino.commit()

            self.res.migrados += 1
            self.res.fotos += len(fotos)

        except (pymysql.MySQLError, RuntimeError) as erro:
            self.destino.rollback()
            self.res.falhas += 1
            mensagem = f"ImoCodigo {imo_origem}: {erro}"
            self.res.erros.append(mensagem)
            try:
                with self.destino.cursor() as cur2:
                    controle.registrar_ocorrencia(
                        cur2, self.op.execucao, imo_origem, OC_FALHA, str(erro)
                    )
                self.destino.commit()
            except pymysql.MySQLError:
                pass
            if self.op.parar_no_erro:
                raise SystemExit(f"Interrompido no primeiro erro -> {mensagem}")
        finally:
            cur.close()

    def _atualizar_contadores(self, cur, imo, novo_imo, fotos, novos_codigos, avisos):
        """ImoFotos, ImoFotoDestaque e ImoFotoUrl sao cache da tfotosimoveis.

        ImoFotoDestaque guarda um FotCodigo. Copiar esse numero da origem e o
        mesmo erro do FotImoCodigo, so mais discreto: apontaria para a foto de
        outro imovel no destino. Tem que ser traduzido pelo mapa.
        """
        if not fotos:
            controle.registrar_ocorrencia(
                cur, self.op.execucao, imo["ImoCodigo"], OC_SEM_FOTOS,
                "imovel sem linhas em tfotosimoveis; ImoFotoUrl da origem preservado",
            )
            cur.execute(
                "UPDATE `timoveis` SET ImoFotos = 0, ImoFotoDestaque = 0 WHERE ImoCodigo = %s",
                (novo_imo,),
            )
            return

        traducao = {a["FotCodigo"]: n for a, n in zip(fotos, novos_codigos)}
        destaque_origem = imo.get("ImoFotoDestaque") or 0
        novo_destaque = traducao.get(destaque_origem)

        if novo_destaque is None:
            # Destaque apontava para foto que nao veio (de outro imovel, ou
            # inexistente). Cai para a primeira foto pela ordem FotPosicao.
            novo_destaque = novos_codigos[0]
            foto_destaque = fotos[0]
            avisos.append((
                OC_DESTAQUE_NAO_RESOLVIDO,
                f"ImoFotoDestaque {destaque_origem} nao esta entre as fotos do imovel; "
                f"usando a primeira (FotCodigo origem {foto_destaque['FotCodigo']})",
            ))
        else:
            foto_destaque = next(f for f in fotos if f["FotCodigo"] == destaque_origem)

        cur.execute(
            "UPDATE `timoveis` SET ImoFotos = %s, ImoFotoDestaque = %s, ImoFotoUrl = %s "
            "WHERE ImoCodigo = %s",
            (len(fotos), novo_destaque, foto_destaque.get("FotUrl") or "", novo_imo),
        )

    # ------------------------------------------------------------------ loop

    def executar(self, ja_feitos, log):
        codigos = self.listar_codigos()
        pendentes = [c for c in codigos if c not in ja_feitos]
        self.res.pulados_mapa = len(codigos) - len(pendentes)

        log(f"recorte ImoCadCodigo={self.op.cad_origem}: {len(codigos)} imoveis na origem")
        log(f"ja migrados (mapa): {self.res.pulados_mapa} | pendentes: {len(pendentes)}")
        if not pendentes:
            return self.res

        inicio = time.time()
        for i in range(0, len(pendentes), self.op.bloco):
            lote = pendentes[i : i + self.op.bloco]
            imoveis = self._buscar_imoveis(lote)
            fotos = self._buscar_fotos(lote)
            for imo in imoveis:
                self._migrar_um(imo, fotos.get(imo["ImoCodigo"], []))

            feitos = min(i + self.op.bloco, len(pendentes))
            decorrido = time.time() - inicio
            ritmo = feitos / decorrido if decorrido > 0 else 0
            log(
                f"  {feitos}/{len(pendentes)} imoveis | {self.res.fotos} fotos | "
                f"{ritmo:.1f} imov/s | falhas={self.res.falhas}"
            )
        return self.res
