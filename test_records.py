import unittest

from record_reader import read_records


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_records()

    def references(self, left, left_key, right, right_key):
        candidates = {row[right_key] for row in self.tables[right]}
        self.assertTrue(all(row[left_key] in candidates for row in self.tables[left]))

    def test_documents_reference_source_records(self):
        self.references('exceptions','task','tasks','task')
        self.references('roster','task','tasks','task')
        self.references('claims','work','work','work')
        self.references('rulings','granted','claims','claim')
        pairs = {(row['task'],row['student']) for row in self.tables['roster']}
        self.assertTrue(all((row['task'],row['student']) in pairs for row in self.tables['steps']))
        self.assertEqual(2, len([row for row in self.tables['claims'] if row['work']=='w1']))
        self.assertTrue(any(row['task']=='t2' and row['acknowledged'] for row in self.tables['deliveries']))
        self.assertEqual(len([row for row in self.tables['exceptions'] if row['task']=='t2']),3)
