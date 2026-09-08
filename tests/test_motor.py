# -*- coding: utf-8 -*-
"""Tests zonder netwerk. Motor.py leest zijn config bij het importeren, dus die
zetten we eerst klaar in een tijdelijke map."""
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="motortest_")
_CFG = os.path.join(_TMP, "config.json")
with open(_CFG, "w", encoding="utf-8") as _f:
    json.dump({"ruislat": 150,
               "hoeken": {"beta": ["term twee"], "alfa": ["term een"]}}, _f)
os.environ["CONFIG"] = _CFG
os.environ["RUWDIR"] = os.path.join(_TMP, "ruw")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor  # noqa: E402


class Config(unittest.TestCase):
    def test_hoeken_komen_uit_het_bestand(self):
        self.assertEqual(sorted(motor.HOEKEN), ["alfa", "beta"])

    def test_volgorde_valt_terug_op_alfabetisch(self):
        self.assertEqual(motor.VOLGORDE, ["alfa", "beta"])

    def test_ruislat(self):
        self.assertEqual(motor.LAT_C, 150)


class Winkelpin(unittest.TestCase):
    def pin(self, link):
        return {"link": link}

    def test_gewone_productpagina_telt(self):
        self.assertTrue(motor.winkelpin(self.pin("https://winkel.nl/products/lamp-a")))
        self.assertTrue(motor.winkelpin(self.pin("https://x.co/product/iets?ref=1")))

    def test_marktplaats_en_affiliate_tellen_niet(self):
        for u in ("https://www.amazon.de/products/x",
                  "https://aliexpress.com/products/x",
                  "https://www.etsy.com/products/x",
                  "https://liketk.it/products/x"):
            self.assertFalse(motor.winkelpin(self.pin(u)), u)

    def test_pagina_zonder_product_in_het_pad_telt_niet(self):
        self.assertFalse(motor.winkelpin(self.pin("https://winkel.nl/collections/alles")))
        self.assertFalse(motor.winkelpin(self.pin("")))

    def test_products_zonder_handle_telt_niet(self):
        self.assertFalse(motor.winkelpin(self.pin("https://winkel.nl/products/")))


class EerderGeoogst(unittest.TestCase):
    def zet(self, inhoud):
        p = os.path.join(_TMP, "gezien.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(inhoud, f)
        motor.GEZIEN_PAD = p

    def test_ontbrekend_bestand_geeft_lege_set(self):
        motor.GEZIEN_PAD = os.path.join(_TMP, "bestaat-niet.json")
        self.assertEqual(motor.eerder_geoogst(), set())

    def test_platte_lijst(self):
        self.zet(["winkel.nl/lamp", "winkel.nl/mok"])
        self.assertEqual(motor.eerder_geoogst(), {"winkel.nl/lamp", "winkel.nl/mok"})

    def test_object_met_producten(self):
        self.zet({"aantal": 1, "producten": ["winkel.nl/lamp"]})
        self.assertEqual(motor.eerder_geoogst(), {"winkel.nl/lamp"})

    def test_stukke_json_breekt_de_ronde_niet(self):
        p = os.path.join(_TMP, "stuk.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{dit is geen json")
        motor.GEZIEN_PAD = p
        self.assertEqual(motor.eerder_geoogst(), set())


class Stilte(unittest.TestCase):
    def test_log_print_niets_zonder_luidop(self):
        """De belangrijkste eigenschap voor een publieke repo: zoektermen mogen
        niet in het workflow-log belanden."""
        import io as _io
        from contextlib import redirect_stdout
        motor.LUIDOP = False
        buf = _io.StringIO()
        with redirect_stdout(buf):
            motor.log("zoek 'geheime term': 12 nieuw")
        self.assertEqual(buf.getvalue(), "")
        self.assertIn("zoek 'geheime term': 12 nieuw", motor.log_regels)


if __name__ == "__main__":
    unittest.main()
