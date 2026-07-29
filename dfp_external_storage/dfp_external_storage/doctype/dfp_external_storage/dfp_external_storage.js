
frappe.ui.form.on('DFP External Storage', {

	setup: frm => {
		frm.button_remote_files_list = null
	},

	refresh: function(frm) {
		if (frm.is_new() && !frm.doc.doctypes_ignored.length) {
			frm.doc.doctypes_ignored.push({doctype_to_ignore: 'Data Import'})
			frm.doc.doctypes_ignored.push({doctype_to_ignore: 'Prepared Report'})
			frm.refresh_field('doctypes_ignored')
		}

		if (frm.doc.enabled) {
			frm.button_remote_files_list = frm.add_custom_button(
				__('List files in bucket'),
				() => frappe.set_route('dfp-s3-bucket-list', frm.doc.name)
				// () => frappe.set_route('dfp-s3-bucket-list', { storage: frm.doc.name })
			)
		}

		frm.set_query('folders', function() {
			return {
				filters: {
					is_folder: 1,
				},
			}
		})

		// Exclude only folders already owned by a DIFFERENT storage, so the
		// "one folder -> one storage" invariant holds without hiding unassigned
		// folders or this storage's own already-assigned folders. `parent` is the
		// owning storage's docname; `folder` is the assigned File folder.
		//
		// `parent_doctype` (NOT `parent`) is the reportview kwarg that scopes a child
		// (istable) doctype query to its parent. Two reasons it is required here:
		//   1. `frappe.db.get_list` forwards every option verbatim to
		//      `frappe.desk.reportview.get_list` -> `DatabaseQuery.execute(**args)`.
		//      `execute()` accepts `parent_doctype` but has NO `parent` kwarg, so the
		//      old `parent:` option 500'd every form refresh with
		//      `TypeError: DatabaseQuery.execute() got an unexpected keyword argument 'parent'`.
		//   2. Without `parent_doctype`, reportview resolves field permission against
		//      the child doctype itself (empty permissions) and strips every field but
		//      `name` — so `parent`/`folder` came back empty and the exclusion never
		//      worked. Scoping to the parent doctype makes both fields readable.
		frappe.db.get_list(
			'DFP External Storage by Folder',
			{fields: ['parent', 'folder'], parent_doctype: 'DFP External Storage'}
		).then(data => {
			let folders_owned_by_other_storages = (data || [])
				.filter(d => d.parent != frm.doc.name)
				.map(d => d.folder)
			frm.set_query('folders', function () {
				return {
					filters: {
						is_folder: 1,
						name: ['not in', folders_owned_by_other_storages],
					},
				}
			})
		})

	},

})
