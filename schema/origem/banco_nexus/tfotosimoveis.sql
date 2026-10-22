-- Origem: banco_nexus.tfotosimoveis
-- Relacionamento: tfotosimoveis.FotImoCodigo -> timoveis.ImoCodigo (sem FK declarada, apenas KEY)
-- Atencao: AUTO_INCREMENT=64730498 -- ordem de grandeza alta, migracao obrigatoriamente em lotes.

CREATE TABLE `tfotosimoveis` (
 `FotCodigo` int(11) NOT NULL AUTO_INCREMENT,
 `FotImoCodigo` int(11) NOT NULL,
 `FotLiberado` varchar(1) COLLATE latin1_general_ci NOT NULL,
 `FotLegenda` text COLLATE latin1_general_ci NOT NULL,
 `FotLegenda2` text COLLATE latin1_general_ci NOT NULL,
 `FotPosicao` int(11) NOT NULL DEFAULT '99',
 `FotDownload` int(11) NOT NULL DEFAULT '0',
 `FotUrl` text COLLATE latin1_general_ci NOT NULL,
 `Fot360` text COLLATE latin1_general_ci,
 PRIMARY KEY (`FotCodigo`),
 KEY `fotoimovel` (`FotImoCodigo`)
) ENGINE=InnoDB AUTO_INCREMENT=64730498 DEFAULT CHARSET=latin1 COLLATE=latin1_general_ci
