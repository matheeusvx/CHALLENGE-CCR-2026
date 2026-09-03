"""Testes dos parsers de ingestao dos dados originais da CCR Motiva.

As fixtures reproduzem as particularidades reais dos arquivos entregues:

* ``Marco km`` e um KMZ de verdade (ZIP) e guarda o km apenas no ``description``;
* ``classificacao_rocada.kmz`` e KML cru apesar da extensao, e os nomes do
  ``Schema`` estao deslocados em relacao aos valores;
* o unifilar e uma matriz (posicao transversal x quilometragem), nao um log.
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

from scripts.ingest_motiva_data import (
    classify_position,
    height_limit_for,
    parse_km_markers,
    parse_mowing_polygons,
    parse_unifilar,
)
from src.satellite_monitoring.database import (
    DEFAULT_HEIGHT_LIMIT_CM,
    STRICT_HEIGHT_LIMIT_CM,
)

KML_MARCOS = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>Marco km</name>
  <Placemark>
    <description>SP021
km 0</description>
    <LookAt><longitude>-1.0</longitude><latitude>-1.0</latitude></LookAt>
    <Point><coordinates>-46.736768,-23.416207,0</coordinates></Point>
  </Placemark>
  <Placemark>
    <description>SP021\t
km 8</description>
    <Point><coordinates>-46.794858,-23.460578,0</coordinates></Point>
  </Placemark>
  <Placemark>
    <description>SP021
km20</description>
    <Point><coordinates>-46.819423,-23.557016,0</coordinates></Point>
  </Placemark>
</Folder></Document></kml>
"""

KML_ROCADA = """<?xml version="1.0" encoding="utf-8" ?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document id="root_doc">
 <Folder><name>classificacao_rocada</name>
  <Placemark>
   <name>Apenas manual</name>
   <description>12</description>
   <ExtendedData><SchemaData schemaUrl="#classificacao_rocada">
     <SimpleData name="classe">-23.41847351</SimpleData>
     <SimpleData name="KM">-46.73185821</SimpleData>
     <SimpleData name="Latitude">1272.70</SimpleData>
   </SchemaData></ExtendedData>
   <Polygon>
    <outerBoundaryIs><LinearRing><coordinates>
      -46.7325,-23.4176 -46.7326,-23.4177 -46.7327,-23.4176 -46.7325,-23.4176
    </coordinates></LinearRing></outerBoundaryIs>
   </Polygon>
  </Placemark>
  <Placemark>
   <name>Spider, com ancoragem</name>
   <description>3</description>
   <ExtendedData><SchemaData schemaUrl="#classificacao_rocada">
     <SimpleData name="classe">-23.42000000</SimpleData>
     <SimpleData name="KM">-46.75000000</SimpleData>
     <SimpleData name="Latitude">178.0</SimpleData>
   </SchemaData></ExtendedData>
   <Polygon>
    <outerBoundaryIs><LinearRing><coordinates>
      -46.75,-23.42 -46.751,-23.421 -46.752,-23.42 -46.75,-23.42
    </coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>
      -46.7505,-23.4205 -46.7506,-23.4206 -46.7507,-23.4205 -46.7505,-23.4205
    </coordinates></LinearRing></innerBoundaryIs>
   </Polygon>
  </Placemark>
 </Folder>
</Document></kml>
"""

SHARED_STRINGS = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="5" uniqueCount="5">
  <si><t>1.1</t></si>
  <si><t>CANT. DISPOSITIVO EXT.</t></si>
  <si><t>1.4</t></si>
  <si><t>CANT. LATERAL EXTERNA</t></si>
  <si><t>X</t></si>
</sst>
"""

SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
  <row r="9">
    <c r="F9"><v>0</v></c>
    <c r="G9"><f>F9+500</f><v>500</v></c>
    <c r="H9"><f>G9+500</f><v>1000</v></c>
  </row>
  <row r="10">
    <c r="A10" t="s"><v>0</v></c>
    <c r="B10" t="s"><v>1</v></c>
    <c r="F10"><v>1</v></c>
    <c r="G10"><v>3</v></c>
    <c r="H10" t="s"><v>4</v></c>
  </row>
  <row r="11">
    <c r="A11" t="s"><v>2</v></c>
    <c r="B11" t="s"><v>3</v></c>
    <c r="F11"><v>2</v></c>
    <c r="G11" t="s"><v>4</v></c>
    <c r="H11"><v>3</v></c>
  </row>
</sheetData></worksheet>
"""


def _write_kmz(path: Path, kml: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("doc.kml", kml)
    return path


def _write_xlsx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", SHARED_STRINGS)
        archive.writestr("xl/worksheets/sheet1.xml", SHEET)
    return path


def test_marcos_km_leem_o_km_do_description_e_ignoram_o_lookat(tmp_path):
    caminho = _write_kmz(tmp_path / "Marco km_rodoanel 2.kmz", KML_MARCOS)
    marcos = parse_km_markers(caminho)

    assert [marco.km for marco in marcos] == [0, 8, 20]
    # A coordenada vem do Point, nunca do LookAt.
    assert marcos[0].longitude == -46.736768
    assert marcos[0].latitude == -23.416207


def test_poligonos_corrigem_o_deslocamento_dos_campos(tmp_path):
    # O arquivo real e KML cru, apesar da extensao .kmz.
    caminho = tmp_path / "classificacao_rocada.kmz"
    caminho.write_text(KML_ROCADA, encoding="utf-8")

    poligonos = parse_mowing_polygons(caminho)
    assert len(poligonos) == 2

    primeiro = poligonos[0]
    assert primeiro.method == "Apenas manual"
    assert primeiro.km == 12
    # "classe" guarda latitude, "KM" guarda longitude e "Latitude" guarda a area.
    assert primeiro.latitude == -23.41847351
    assert primeiro.longitude == -46.73185821
    assert primeiro.area_m2 == 1272.70
    assert primeiro.geometry["type"] == "Polygon"
    assert len(primeiro.geometry["coordinates"]) == 1

    segundo = poligonos[1]
    assert segundo.method == "Spider, com ancoragem"
    # O anel interno (buraco) e preservado.
    assert len(segundo.geometry["coordinates"]) == 2


def test_unifilar_vira_matriz_posicao_por_quilometragem(tmp_path):
    caminho = _write_xlsx(tmp_path / "RA-RET-ROC-LIMP-2026-03-13.xlsx")
    observado_em, condicoes = parse_unifilar(caminho)

    assert observado_em == date(2026, 3, 13)
    assert len(condicoes) == 6

    por_chave = {
        (item.position_code, item.chainage_m): item.height_class for item in condicoes
    }
    assert por_chave[("1.1", 0)] == "1"
    assert por_chave[("1.1", 500)] == "3"
    assert por_chave[("1.1", 1000)] == "X"
    assert por_chave[("1.4", 0)] == "2"
    assert por_chave[("1.4", 500)] == "X"
    assert por_chave[("1.4", 1000)] == "3"

    descricoes = {item.position_code: item.position_description for item in condicoes}
    assert descricoes["1.1"] == "CANT. DISPOSITIVO EXT."


def test_unifilar_sem_data_no_nome_nao_quebra(tmp_path):
    caminho = _write_xlsx(tmp_path / "sem-data.xlsx")
    observado_em, condicoes = parse_unifilar(caminho)
    assert observado_em is None
    assert condicoes


def test_classificacao_da_posicao_transversal():
    assert classify_position("CANT. DISPOSITIVO EXT.") == ("externa", "dispositivo")
    assert classify_position("CANT. MARGINAL INTERNA") == ("interna", "marginal")
    assert classify_position("CANT. LATERAL EXTERNA") == ("externa", "lateral")
    assert classify_position("CANT. CENTRAL INTERNA") == ("interna", "central")
    assert classify_position("PISTA EXTERNA") == ("externa", "pista")


def test_limite_contratual_por_tipo_de_posicao():
    limite_dispositivo, base_dispositivo = height_limit_for("dispositivo")
    limite_lateral, base_lateral = height_limit_for("lateral")

    assert limite_dispositivo == STRICT_HEIGHT_LIMIT_CM
    assert limite_lateral == DEFAULT_HEIGHT_LIMIT_CM
    assert "10 cm" in base_dispositivo
    assert "30 cm" in base_lateral
