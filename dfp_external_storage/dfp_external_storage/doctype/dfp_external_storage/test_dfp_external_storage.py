# Copyright (c) 2023, DFP and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from dfp_external_storage.dfp_external_storage.doctype.dfp_external_storage.dfp_external_storage import (
	DFPExternalStorageFile,
	hook_file_after_delete,
	hook_file_before_save,
)


class TestDFPExternalStorage(FrappeTestCase):
	def test_reposting_temp_files_stay_local(self):
		"""framework#70: ERPNext stock reposting writes an internal .json.gz temp
		File (attached_to_field == "reposting_data_file") and reads it back via
		File.get_full_path(), which needs a LOCAL path. dfp must NEVER upload
		these to external storage — otherwise the url becomes "/file/..." and
		reposting fails with "Cannot access file path". The ignore guard must key
		on the field marker, regardless of the connection's doctypes_ignored list.
		"""
		# A reposting temp file → always ignored (kept local), even with no
		# connection configured on the file.
		repost_file = SimpleNamespace(
			attached_to_field="reposting_data_file",
			attached_to_doctype="Repost Item Valuation",
			dfp_external_storage_doc=None,
		)
		self.assertTrue(
			DFPExternalStorageFile.dfp_external_storage_ignored_doctypes(repost_file)
		)

		# A normal attachment with no connection is NOT force-ignored by this
		# guard (the method returns a falsy value → external storage may apply).
		normal_file = SimpleNamespace(
			attached_to_field="", attached_to_doctype="Sales Invoice", dfp_external_storage_doc=None
		)
		self.assertFalse(
			bool(DFPExternalStorageFile.dfp_external_storage_ignored_doctypes(normal_file))
		)

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
