# Copyright (c) 2023, DFP and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from dfp_external_storage.dfp_external_storage.doctype.dfp_external_storage.dfp_external_storage import (
	hook_file_after_delete,
	hook_file_before_save,
)


class TestDFPExternalStorage(FrappeTestCase):
	def test_hooks_skip_non_dfp_files(self):
		"""framework#120: the global File doc_event hooks must early-return on Files
		that lack the DFP override methods (a base File created where the class
		override did not apply) instead of raising AttributeError and breaking the
		other app's File creation/deletion.

		Stand-in: a File-like object WITHOUT dfp_external_storage_upload_file /
		dfp_external_storage_delete_file. Pre-fix both hooks raised AttributeError;
		the hasattr guard makes them no-ops.
		"""
		non_dfp_file = SimpleNamespace()
		self.assertFalse(hasattr(non_dfp_file, "dfp_external_storage_upload_file"))
		self.assertIsNone(hook_file_before_save(non_dfp_file, "before_save"))
		self.assertIsNone(hook_file_after_delete(non_dfp_file, "after_delete"))
