# Copyright (c) 2023, DFP and Contributors
# See license.txt

from types import SimpleNamespace

import urllib3
from frappe.tests.utils import FrappeTestCase

import dfp_external_storage.dfp_external_storage.doctype.dfp_external_storage.dfp_external_storage as dfp_mod
from dfp_external_storage.dfp_external_storage.doctype.dfp_external_storage.dfp_external_storage import (
	DFP_S3_CONNECT_TIMEOUT,
	DFP_S3_READ_TIMEOUT,
	DFP_S3_RETRIES,
	DFPExternalStorageFile,
	MinioConnection,
	dfp_bounded_http_client,
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

	def test_bounded_http_client_timeouts(self):
		"""framework#95 (origin Floreer-Africa/framework#126): dfp streams every
		external-storage image through the gunicorn worker, and minio's default
		urllib3 pool waits 300s with 5 retries — a stalled S3 endpoint therefore
		pins a worker far past its request timeout and a burst of cold-cache fetches
		can exhaust the pool and take a site down. dfp_bounded_http_client() must
		return a urllib3 pool whose timeouts/retries are the short, fail-fast values,
		so a stall frees the worker instead of pinning it.
		"""
		pool = dfp_bounded_http_client()

		self.assertIsInstance(pool, urllib3.PoolManager)

		timeout = pool.connection_pool_kw["timeout"]
		self.assertIsInstance(timeout, urllib3.Timeout)
		self.assertEqual(timeout.connect_timeout, DFP_S3_CONNECT_TIMEOUT)
		self.assertEqual(timeout._read, DFP_S3_READ_TIMEOUT)

		retries = pool.connection_pool_kw["retries"]
		self.assertIsInstance(retries, urllib3.Retry)
		self.assertEqual(retries.total, DFP_S3_RETRIES)

	def test_minio_connection_wires_bounded_client(self):
		"""The bounded pool is useless unless MinioConnection actually hands it to
		the minio Client, so a real deployment gets the fail-fast timeouts. Capture
		the http_client kwarg the constructor passes to Minio (no network needed) and
		assert it is a bounded pool carrying framework#95's timeouts.
		"""
		captured = {}

		def fake_minio(**kwargs):
			captured.update(kwargs)
			return SimpleNamespace(**kwargs)

		original_minio = dfp_mod.Minio
		dfp_mod.Minio = fake_minio
		try:
			MinioConnection(
				endpoint="s3.example.test",
				access_key="ak",
				secret_key="sk",
				region="us-east-1",
				secure=True,
			)
		finally:
			dfp_mod.Minio = original_minio

		http_client = captured.get("http_client")
		self.assertIsInstance(http_client, urllib3.PoolManager)
		timeout = http_client.connection_pool_kw["timeout"]
		self.assertEqual(timeout.connect_timeout, DFP_S3_CONNECT_TIMEOUT)
		self.assertEqual(timeout._read, DFP_S3_READ_TIMEOUT)

	def test_longest_prefix_basic_and_empty(self):
		"""framework#102: file-type routing matches a File's guessed mime type
		against a storage's newline-separated `route_mimetypes_starting` prefixes.
		The matcher must strip whitespace, drop blank lines, return the LONGEST
		matching prefix (most specific wins), and return None when either input is
		empty or nothing matches (feature off)."""
		# matches, strips whitespace, drops blank lines
		self.assertEqual(
			dfp_mod.dfp_mimetype_longest_prefix("image/png", " image/ \n\n video/ "),
			"image/",
		)
		# most-specific (longest) prefix wins within one storage's list
		self.assertEqual(
			dfp_mod.dfp_mimetype_longest_prefix("image/png", "image/\nimage/png"),
			"image/png",
		)
		# no match
		self.assertIsNone(dfp_mod.dfp_mimetype_longest_prefix("application/pdf", "image/"))
		# empty inputs => feature off
		self.assertIsNone(dfp_mod.dfp_mimetype_longest_prefix("image/png", ""))
		self.assertIsNone(dfp_mod.dfp_mimetype_longest_prefix(None, "image/"))

	def test_route_by_mimetype_precedence(self):
		"""framework#102: across ENABLED storages, the storage with the longest
		matching prefix wins; an exact-length tie breaks on the lowest storage name
		so routing is always deterministic. No match / empty => None (legacy)."""
		# longest prefix across storages wins (most specific)
		self.assertEqual(
			dfp_mod.dfp_storage_route_by_mimetype(
				"image/png", [("A", "image/"), ("B", "image/png")]
			),
			"B",
		)
		# equal-length tie => lowest name wins (deterministic)
		self.assertEqual(
			dfp_mod.dfp_storage_route_by_mimetype(
				"image/png", [("Z", "image/"), ("A", "image/")]
			),
			"A",
		)
		# no match / empty inputs
		self.assertIsNone(
			dfp_mod.dfp_storage_route_by_mimetype("application/pdf", [("A", "image/")])
		)
		self.assertIsNone(dfp_mod.dfp_storage_route_by_mimetype(None, [("A", "image/")]))
		self.assertIsNone(dfp_mod.dfp_storage_route_by_mimetype("image/png", []))

	def _run_resolver(self, *, explicit, mimetype, folder, storages):
		"""Drive DFPExternalStorageFile.dfp_external_storage_doc with fakes.

		`storages` maps storage name -> {"enabled", "route_mimetypes_starting"}.
		Emulates the enabled + `route_mimetypes_starting IS SET` query filter and
		returns the resolved storage name (or None). No DB, no network.
		"""
		fake_self = SimpleNamespace(
			dfp_external_storage=explicit,
			folder=folder,
			dfp_mime_type_guess_by_file_name=mimetype,
		)

		def fake_get_all(doctype, filters=None, fields=None, **kw):
			filters = filters or {}
			rows = []
			for name, cfg in storages.items():
				if filters.get("enabled") == 1 and not cfg["enabled"]:
					continue
				if filters.get("route_mimetypes_starting") == ["is", "set"] and not cfg["route_mimetypes_starting"]:
					continue
				rows.append(SimpleNamespace(
					name=name, route_mimetypes_starting=cfg["route_mimetypes_starting"]))
			return rows

		def fake_get_doc(doctype, name):
			return SimpleNamespace(name=name)

		def fake_get_value(doctype, fieldname=None, filters=None, **kw):
			# Folder routing fallback is not exercised by these tests (no storage is
			# folder-bound here), so return None to drive the type/Home paths.
			return None

		orig = (dfp_mod.frappe.get_all, dfp_mod.frappe.get_doc, dfp_mod.frappe.db.get_value)
		dfp_mod.frappe.get_all, dfp_mod.frappe.get_doc, dfp_mod.frappe.db.get_value = (
			fake_get_all, fake_get_doc, fake_get_value)
		try:
			# Call the raw cached_property functions to bypass instance caching.
			by_mime = DFPExternalStorageFile.dfp_external_storage_doc_by_mimetype.func(fake_self)
			fake_self.dfp_external_storage_doc_by_mimetype = by_mime
			doc = DFPExternalStorageFile.dfp_external_storage_doc.func(fake_self)
		finally:
			dfp_mod.frappe.get_all, dfp_mod.frappe.get_doc, dfp_mod.frappe.db.get_value = orig
		return doc.name if doc else None

	def test_type_routing_matches_enabled_storage(self):
		"""framework#102 T1: an image with a storage configured for image/ routes
		to that storage (no explicit selection, no folder binding)."""
		self.assertEqual(self._run_resolver(
			explicit=None, mimetype="image/png", folder="Home",
			storages={"A": {"enabled": 1, "route_mimetypes_starting": "image/"}},
		), "A")

	def test_explicit_field_wins_over_type(self):
		"""framework#102 T3: an explicit dfp_external_storage on the File wins over
		type routing — the mime resolver must never be consulted."""
		self.assertEqual(self._run_resolver(
			explicit="EXPLICIT", mimetype="image/png", folder="Home",
			storages={"A": {"enabled": 1, "route_mimetypes_starting": "image/"}},
		), "EXPLICIT")

	def test_disabled_storage_never_routed(self):
		"""framework#102 T4: a disabled storage is never selected even if its prefix
		matches (the query filters enabled=1)."""
		self.assertIsNone(self._run_resolver(
			explicit=None, mimetype="image/png", folder="Home",
			storages={"A": {"enabled": 0, "route_mimetypes_starting": "image/"}},
		))

	def test_nonmatching_type_falls_through(self):
		"""framework#102 T2/T6: a non-matching mime type yields no type match, so
		resolution falls through to folder/Home (here None => legacy behaviour)."""
		self.assertIsNone(self._run_resolver(
			explicit=None, mimetype="application/pdf", folder="Home",
			storages={"A": {"enabled": 1, "route_mimetypes_starting": "image/"}},
		))

	def test_type_routing_needs_no_folder_assignment(self):
		"""framework#102 (addendum): a storage with NO folder assigned but a matching
		`route_mimetypes_starting` is still resolved for a matching file. `_run_resolver`
		makes the folder lookup return None (no DFP-by-folder rows), so only the mime
		route can select storage A — proving folder assignment is optional for routing."""
		self.assertEqual(self._run_resolver(
			explicit=None, mimetype="image/png", folder="Some/Unbound/Folder",
			storages={"A": {"enabled": 1, "route_mimetypes_starting": "image/"}},
		), "A")

	def _make_storage(self, bucket, folder):
		"""Insert a DFP External Storage bound to `folder`, network-isolated.

		validate_bucket() is stubbed for the whole test (patched in the test body)
		so no S3 connection is attempted. FrappeTestCase rolls the transaction back
		in tearDown, so the storage + its child row never persist.
		"""
		import frappe

		doc = frappe.get_doc({
			"doctype": "DFP External Storage",
			"title": bucket,
			"type": "S3 Compatible",
			"endpoint": "s3.example.test",
			"region": "us-east-1",
			"bucket_name": bucket,
			"folders": [{"folder": folder}],
		})
		doc.insert(ignore_permissions=True)
		return doc

	def test_folder_picker_query_contract(self):
		"""framework#102 regression (follow-up to bug-153): the DFP External Storage
		form's folder-picker reads the folder→storage assignment map with
		``frappe.db.get_list('DFP External Storage by Folder', …)``, which forwards
		every option verbatim to ``frappe.desk.reportview.get_list`` →
		``DatabaseQuery.execute(**args)``.

		Two defects the fix must lock in:
		  1. Passing ``parent`` (as bug-153 did) crashes every form refresh —
		     ``execute()`` has no ``parent`` kwarg → TypeError. The picker used
		     ``parent: 'DFP External Storage'``; the fix uses ``parent_doctype``.
		  2. Without ``parent_doctype`` the child (istable) doctype's fields are
		     stripped to ``name`` only (permission resolves against the child, whose
		     ``permissions`` is empty), so ``parent``/``folder`` come back empty and
		     the "exclude folders owned by another storage" logic can never work.
		     ``parent_doctype`` scopes permission to the parent and makes them readable.

		Verified FAIL-before / PASS-after: with ``parent`` the call raises (500 in
		prod); with ``parent_doctype`` both fields are returned and the ownership
		exclusion the JS performs selects the correct folders.
		"""
		import frappe
		from frappe.desk import reportview

		# Network isolation: never touch S3 while inserting fixtures.
		original_validate_bucket = dfp_mod.DFPExternalStorage.validate_bucket
		dfp_mod.DFPExternalStorage.validate_bucket = lambda self: None
		try:
			folder_a = frappe.get_doc({
				"doctype": "File", "file_name": "dfp-test-folder-a",
				"is_folder": 1, "folder": "Home",
			}).insert(ignore_permissions=True).name
			folder_b = frappe.get_doc({
				"doctype": "File", "file_name": "dfp-test-folder-b",
				"is_folder": 1, "folder": "Home",
			}).insert(ignore_permissions=True).name

			storage_a = self._make_storage("dfp-test-a", folder_a).name
			storage_b = self._make_storage("dfp-test-b", folder_b).name

			# Defect 1: the bug-153 kwarg crashes — this is the exact production 500.
			with self.assertRaises(TypeError):
				reportview.execute(
					"DFP External Storage by Folder",
					fields=["parent", "folder"],
					parent="DFP External Storage",
				)

			# Defect 2 + fix: parent_doctype returns the real child fields.
			rows = reportview.execute(
				"DFP External Storage by Folder",
				fields=["parent", "folder"],
				parent_doctype="DFP External Storage",
			)
			by_folder = {r["folder"]: r["parent"] for r in rows}
			self.assertEqual(by_folder.get(folder_a), storage_a)
			self.assertEqual(by_folder.get(folder_b), storage_b)

			# The exclusion the JS performs on those rows: for storage A the picker
			# must hide only folder_b (owned by B), never folder_a (its own).
			owned_by_others = [r["folder"] for r in rows if r["parent"] != storage_a]
			self.assertIn(folder_b, owned_by_others)
			self.assertNotIn(folder_a, owned_by_others)
		finally:
			dfp_mod.DFPExternalStorage.validate_bucket = original_validate_bucket
