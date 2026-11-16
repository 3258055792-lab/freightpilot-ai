from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from freight_tools import FreightTools, DATABASE, dispatch

class FreightToolTests(unittest.TestCase):
 def setUp(self):
  self.service=FreightTools();self.addCleanup(self.service.close)
 def calc(self,v='5',w='100',**kwargs):
  return self.service.calculate('普货三类',v,w,**kwargs)
 def test_boundaries_and_whole_shipment(self):
  for volume,band,base in [('0.5','1-5CBM','1100.00'),('5','1-5CBM','5500.00'),('5.001','5-10CBM','5351.07'),('10','5-10CBM','10700.00'),('10.001','10+CBM','10501.05')]:
   with self.subTest(volume=volume):
    result=self.calc(volume)
    self.assertEqual((result['band'],result['base_freight_usd']),(band,base))
    self.assertIsNone(result['total_payable']);self.assertFalse(result['usable_for_quote'])
 def test_overweight_uses_actual_volume_even_below_minimum(self):
  self.assertEqual(self.calc('0.5','151')['status'],'blocked')
  self.assertIsNone(self.calc('1','301')['base_freight_usd'])
  self.assertEqual(self.calc('1','300')['base_freight_usd'],'1100.00')
 def test_fees_and_currency_separation(self):
  r=self.calc(fumigation=True,hinge_count=2,pallet_foot_count=3)
  self.assertEqual(r['known_subtotals'],{'USD':'5500.00','CNY':'1180.00'})
  self.assertFalse(r['currency_conversion_applied'])
  self.assertEqual(len(r['selected_fees']),3)
 def test_bad_numbers(self):
  for x in ('NaN','Infinity','-1','0',True,'1.1234567'):
   with self.subTest(x=x),self.assertRaises(ValueError):self.calc(x)
  with self.assertRaises(ValueError):self.calc(hinge_count=-1)
 def test_non_numeric_rates_are_not_zero(self):
  for c in ['十一类','十二类']:
   r=self.service.calculate(c,'1','100')
   self.assertEqual(r['status'],'blocked');self.assertIsNone(r['base_freight_usd'])
 def test_lookup_across_workbook_and_provenance(self):
  self.assertEqual(len(self.service.lookup()['sheets']),17)
  r=self.service.lookup(sheet='巴西海运双清包税',cell='B21')
  self.assertEqual(r['matches'],1);self.assertIn('300KG',r['results'][0]['raw_text'])
  r=self.service.lookup(sheet='墨西哥空运专线',limit=1)
  self.assertEqual(len(r['results']),1);self.assertEqual(r['next_offset'],1)
  self.assertEqual(self.service.lookup(query="' OR 1=1 --")['matches'],0)
 def test_cell_requires_sheet(self):
  with self.assertRaises(ValueError):self.service.lookup(cell='B21')
 def test_original_database_unchanged(self):
  before=hashlib.sha256(DATABASE.read_bytes()).hexdigest()
  self.calc();self.service.lookup(query='附加')
  self.assertEqual(before,hashlib.sha256(DATABASE.read_bytes()).hexdigest())
  with self.assertRaises(sqlite3.OperationalError): self.service.db.execute('DELETE FROM pending_prices')
 def test_multiple_versions_and_missing_confirmation_fail(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'test.db'
   path.write_bytes(DATABASE.read_bytes())
   with closing(sqlite3.connect(path)) as db, db:db.execute('DELETE FROM user_rule_confirmations')
   with self.assertRaises(ValueError):dispatch('freight_calculate',{'category':'普货一类','volume_cbm':'1','weight_kg':'100'},path)
   with closing(sqlite3.connect(path)) as db, db:
    db.execute("INSERT INTO workbook_archives(source_path,sha256,original_xlsx) VALUES('other','other',X'00')")
   with self.assertRaises(ValueError):dispatch('freight_lookup',{'query':'CBM'},path)

if __name__=='__main__':unittest.main()
