"""Help → Contribute electrode data… dialog.

Lets the user upload their accumulated electrode-potential measurement
samples to the project's GitHub repository so the maintainer can
refine the catalog defaults from real-world data. The flow is
deliberately privacy-first:

* **Anonymous by default.** No notebook names, save-paths, user names,
  or email addresses are included in the payload until the user
  explicitly checks an opt-in box.
* **Show the payload.** The dialog renders the exact JSON that would
  be uploaded in a read-only preview pane so the user can verify
  there's nothing in it they didn't expect.
* **No silent uploads.** The dialog never sends anything by itself —
  it builds a pre-filled GitHub Issue URL and opens it in the user's
  browser. The user reviews, edits, and clicks "Submit new issue"
  on github.com themselves; the project repository is the
  destination of record.
* **Save-to-file fallback.** Lab IT policy may forbid posting on a
  public site directly from a workstation. The dialog also offers a
  "Save to file" button that writes the same JSON to disk so the user
  can attach it via whatever process their institution allows.

Why GitHub Issues rather than a custom server?

* Zero infrastructure to stand up; the project repo already exists.
* Public, citable, threaded — academic users get attribution and a
  visible record of their contribution.
* The maintainer can run a small ingest script (e.g. by parsing each
  contribution issue's body via :func:`stimtest.electrode_potential_history.import_payload`)
  to absorb new data into a development build.

The opt-in attribution fields (user name, email, session name,
institution) are toggled INDEPENDENTLY — academic users typically
want to be credited but might prefer to keep their email private,
for instance. Each toggle gates exactly one field; an opt-in with
no value behind it stays off.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import quote

from PyQt6 import QtCore, QtGui, QtWidgets


#: Repository where contribution issues are posted. Matches the
#: ``Help → Check for updates…`` URL so a user with one bookmarked
#: lands on the same project page. Configurable via
#: :func:`set_repository` if a future fork wants to redirect.
DEFAULT_REPO_OWNER = "Bortz1234"
DEFAULT_REPO_NAME = "StimulationTesting"

#: Maximum body length the GitHub "new issue" URL accepts before the
#: server returns 414 (URI Too Long). The actual cutoff is around
#: 8 KB; 7000 leaves headroom for the encoded title + tracking
#: parameters. Above this we surface a warning telling the user to
#: use the Save-to-file path and attach the JSON manually instead of
#: shoving everything into the URL.
GITHUB_URL_BODY_BUDGET = 7000


def github_new_issue_url(owner: str, repo: str, *,
                          title: str, body: str,
                          labels: Optional[list] = None) -> str:
    """Return ``https://github.com/<owner>/<repo>/issues/new?…``
    with title + body + labels percent-encoded.

    GitHub's new-issue endpoint accepts ``title`` / ``body`` /
    ``labels`` / ``assignees`` query parameters; the user lands on
    a normal new-issue form pre-populated with whatever we pass.
    They review and submit themselves — we never POST on their
    behalf, which would require a personal access token and bypass
    review.

    The function is pure (no I/O), so it's easy to unit-test and
    safe to call from the GUI thread.
    """
    params = [
        ("title", title),
        ("body", body),
    ]
    if labels:
        # GitHub accepts a comma-separated string of label names;
        # the labels must already exist on the target repository
        # or the URL will succeed but the labels stay unset.
        params.append(("labels", ",".join(labels)))
    encoded = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return f"https://github.com/{owner}/{repo}/issues/new?{encoded}"


def _markdown_summary(payload: dict) -> str:
    """Render a human-readable summary table of the payload's bins.

    Goes into the GitHub Issue body above the JSON code-block so a
    reviewer can see at a glance what's being contributed without
    reading the raw JSON. Format mirrors the per-bin summary the
    maintainer would want to triage with: coating, sample count,
    mean, min, max.
    """
    lines: list = []
    lines.append("| Coating | Samples | Mean V | Min V | Max V |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for key, samples in sorted((payload.get("samples") or {}).items()):
        if not samples:
            continue
        vals = [float(s["v"]) for s in samples
                if isinstance(s, dict) and "v" in s]
        if not vals:
            continue
        mean_v = sum(vals) / len(vals)
        lines.append(
            f"| `{key}` | {len(vals)} | {mean_v:+.4f} | "
            f"{min(vals):+.4f} | {max(vals):+.4f} |"
        )
    return "\n".join(lines)


def build_issue_body(payload: dict) -> str:
    """Compose the Markdown body for the GitHub Issue.

    Includes:

    1. A header line that flags the issue as a data contribution so
       the maintainer's labels / search filters can pick it up.
    2. A summary table (``_markdown_summary``) for at-a-glance
       triage.
    3. A fenced JSON block carrying the full payload — this is what
       :func:`stimtest.electrode_potential_history.import_payload`
       reads back when the maintainer absorbs the contribution.
    4. A literal "How this is used" footer so a contributor reading
       their own pre-filled issue understands what happens next.

    The resulting string is what gets percent-encoded into the
    issue URL or written to disk.
    """
    summary = _markdown_summary(payload)
    n_total = sum(len(v) for v in (payload.get("samples") or {}).values()
                  if isinstance(v, list))
    n_bins = len(payload.get("samples") or {})
    pretty_json = json.dumps(payload, indent=2, sort_keys=True)
    parts: list = [
        "## Electrode-potential data contribution",
        "",
        f"Contributed via the PULSAR GUI's "
        f"`Help → Contribute electrode data…` flow "
        f"(format **{payload.get('format', '?')}**, "
        f"version **{payload.get('format_version', '?')}**, "
        f"PULSAR **{payload.get('stimtest_version', '?')}**).",
        "",
        f"**{n_total}** samples across **{n_bins}** coating bin(s).",
        "",
        summary,
        "",
        "<details><summary>Full JSON payload (click to expand)</summary>",
        "",
        "```json",
        pretty_json,
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
        "*The maintainer absorbs this dataset via "
        "`stimtest.electrode_potential_history.import_payload`; the "
        "running mean of the pooled samples then informs the catalog "
        "defaults shipped in a future release.*",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ContributeDialog(QtWidgets.QDialog):
    """Modal dialog driving the upload flow.

    ``setup_tab`` is the live :class:`SetupTab` instance — we use it
    to read the user's stored identity (name / email / institution
    / session subject) so the opt-in attribution toggles default to
    pre-populated values where the user has provided them. The
    dialog never writes back to the setup tab.
    """

    # Default labels for the contribution issue. The maintainer can
    # filter / triage by these on the receiving end.
    DEFAULT_LABELS = ["data-contribution", "electrode-potential"]

    def __init__(self,
                 setup_tab,
                 *,
                 repo_owner: str = DEFAULT_REPO_OWNER,
                 repo_name: str = DEFAULT_REPO_NAME,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Contribute electrode data")
        self.setMinimumWidth(720)
        self._setup_tab = setup_tab
        self._repo_owner = repo_owner
        self._repo_name = repo_name

        # Read once — the live SetupTab values are pulled at
        # construction time. If the user changes them mid-dialog
        # the toggles still have the original values; that's
        # consistent with how the rest of the GUI snapshots
        # identity (see SetupTab.setup_snapshot).
        self._user_name = setup_tab.current_user_name() if setup_tab else ""
        self._user_email = setup_tab.current_user_email() if setup_tab else ""
        self._user_institution = (
            setup_tab.current_user_institution() if setup_tab else "")
        self._session_name = (
            setup_tab.current_session_subject() if setup_tab else "")

        # ------------------------------------------------------------- summary
        from ..electrode_potential_history import (
            MIN_SAMPLES_FOR_LEARNED_OCP, summary)
        bins = summary()
        n_total = sum(n for _, n, _ in bins)

        intro = QtWidgets.QLabel(
            "<h3>Contribute electrode-potential data</h3>"
            "<p>Help refine the catalog defaults shipped to every "
            "user of PULSAR. The samples below were "
            "collected from the E_ret trace before and after each "
            "active pulse during your runs.</p>"
            f"<p>You have <b>{n_total}</b> sample(s) across "
            f"<b>{len(bins)}</b> coating bin(s). PtIr alloys are "
            "pooled into a single bin until enough data shows the "
            "alloys behave differently.</p>"
            "<p>Once you click <b>Open GitHub Issue</b> below the "
            "dialog opens your browser at a pre-filled issue form "
            "on the project repository. Review the body, edit if "
            "needed, and click <i>Submit new issue</i> on GitHub "
            "yourself — this dialog does not upload anything "
            "automatically.</p>")
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        # ------------------------------------------------------------- bins table
        self.bins_table = QtWidgets.QTableWidget(len(bins), 3)
        self.bins_table.setHorizontalHeaderLabels(
            ["Coating", "Samples", "Learned OCP (V)"])
        self.bins_table.horizontalHeader().setStretchLastSection(True)
        self.bins_table.verticalHeader().setVisible(False)
        self.bins_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, (key, n, ocp) in enumerate(bins):
            self.bins_table.setItem(row, 0, QtWidgets.QTableWidgetItem(key))
            badge = (f"{n}"
                     if n >= MIN_SAMPLES_FOR_LEARNED_OCP
                     else f"{n} / {MIN_SAMPLES_FOR_LEARNED_OCP}")
            self.bins_table.setItem(row, 1, QtWidgets.QTableWidgetItem(badge))
            ocp_text = (f"{ocp:+.4f}" if ocp is not None
                        else "(not enough samples)")
            self.bins_table.setItem(row, 2, QtWidgets.QTableWidgetItem(ocp_text))
        self.bins_table.resizeColumnsToContents()
        self.bins_table.setMaximumHeight(180)

        # ------------------------------------------------------------- attribution
        attr_box = QtWidgets.QGroupBox(
            "Attribution (optional — academic-credit fields)")
        attr_layout = QtWidgets.QFormLayout(attr_box)
        attr_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        # Per checkbox = single field, gated independently. Default
        # is OFF for every checkbox so the payload stays anonymous
        # unless the user opts in. Placeholder text shows what's
        # already known from the Setup tab so the user understands
        # exactly what would be sent if they tick the box.
        self.cb_name = QtWidgets.QCheckBox("Include my name")
        self.cb_name.setToolTip(
            "Adds your name to the contribution payload. The name "
            "is taken from the User name field on the Setup tab.")
        self.lbl_name = QtWidgets.QLabel(self._user_name or "<i>(empty)</i>")
        self.lbl_name.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.cb_email = QtWidgets.QCheckBox("Include my email")
        self.cb_email.setToolTip(
            "Adds your email to the contribution payload. Useful if "
            "the maintainer wants to follow up about a particularly "
            "interesting sample.")
        self.lbl_email = QtWidgets.QLabel(self._user_email or "<i>(empty)</i>")
        self.lbl_email.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.cb_institution = QtWidgets.QCheckBox("Include institution/company")
        self.cb_institution.setToolTip(
            "Adds your lab / company affiliation to the payload. "
            "Pre-populated from the Institution/Company field on the "
            "Setup tab; you can edit the value here if you'd like a "
            "different attribution string just for this submission.")
        self.lbl_institution = QtWidgets.QLineEdit(self._user_institution)
        self.lbl_institution.setToolTip(
            "Attribution string included with the contribution "
            "when 'Include institution/company' is ticked. Pre-populated "
            "from the Institution/Company field on the Setup tab; edit "
            "here for a one-off override (e.g. you're submitting "
            "on behalf of a collaborator at a different lab).")
        self.lbl_institution.setPlaceholderText(
            "e.g. Neural Interfaces Lab, University of Texas at Dallas")
        # Keep the Institution field disabled until its checkbox is
        # ticked — otherwise the empty-state placeholder reads as
        # "type here" but nothing of the user's input ends up in the
        # payload.
        self.lbl_institution.setEnabled(False)
        self.cb_institution.toggled.connect(self.lbl_institution.setEnabled)
        self.cb_session = QtWidgets.QCheckBox("Include session name")
        self.cb_session.setToolTip(
            "Adds the Session field's value (the per-session subject "
            "/ electrode label, e.g. ``electrode_a1``) to the payload. "
            "Notebook names, save paths, and any other identifying "
            "metadata are NEVER included regardless of which boxes "
            "you tick.")
        self.lbl_session = QtWidgets.QLabel(self._session_name or "<i>(empty)</i>")
        self.lbl_session.setTextFormat(QtCore.Qt.TextFormat.RichText)
        attr_layout.addRow(self.cb_name, self.lbl_name)
        attr_layout.addRow(self.cb_email, self.lbl_email)
        attr_layout.addRow(self.cb_institution, self.lbl_institution)
        attr_layout.addRow(self.cb_session, self.lbl_session)

        # Optional free-text note (e.g. "fresh SIROF coatings, lot #123").
        self.note_input = QtWidgets.QPlainTextEdit()
        self.note_input.setPlaceholderText(
            "Optional note — e.g. coating provenance, electrode "
            "geometry caveats, anything else worth flagging to the "
            "maintainer.")
        self.note_input.setFixedHeight(70)

        # ------------------------------------------------------------- preview
        # Live read-only preview so the user can see EXACTLY what
        # would be uploaded. Auto-refreshed by ``_refresh_preview``
        # whenever any control changes — ties to every checkbox /
        # text edit below.
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QtGui.QFont("Consolas", 9))
        self.preview.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_label = QtWidgets.QLabel(
            "<b>Payload preview</b> — exactly the JSON that would "
            "be uploaded:")
        self.preview_label.setTextFormat(QtCore.Qt.TextFormat.RichText)

        # Wire every input to refresh the preview.
        for w in (self.cb_name, self.cb_email,
                  self.cb_institution, self.cb_session):
            w.toggled.connect(self._refresh_preview)
        self.lbl_institution.textChanged.connect(self._refresh_preview)
        self.note_input.textChanged.connect(self._refresh_preview)

        # ------------------------------------------------------------- buttons
        btn_open = QtWidgets.QPushButton("Open GitHub Issue")
        btn_open.setToolTip(
            "Open your default browser at a pre-filled new-issue "
            "form on the project repository. You'll review and "
            "click Submit on GitHub yourself — this dialog never "
            "uploads anything automatically.")
        btn_open.clicked.connect(self._on_open_github_issue)
        btn_save = QtWidgets.QPushButton("Save to file…")
        btn_save.setToolTip(
            "Save the payload as a JSON file you can attach manually "
            "via your institution's preferred channel.")
        btn_save.clicked.connect(self._on_save_to_file)
        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btn_close.setDefault(True)

        # Enable the open-on-GitHub button only when there's data
        # worth contributing. An empty payload would just clutter
        # the project's issue tracker.
        if n_total == 0:
            btn_open.setEnabled(False)
            btn_save.setEnabled(False)
            btn_open.setToolTip(
                "No samples in the local store yet. Run an experiment "
                "with E_ret captured for at least one capture before "
                "contributing.")

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_save)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)

        # ------------------------------------------------------------- layout
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(intro)
        v.addWidget(self.bins_table)
        v.addWidget(attr_box)
        v.addWidget(QtWidgets.QLabel("<b>Optional note:</b>"))
        v.addWidget(self.note_input)
        v.addWidget(self.preview_label)
        v.addWidget(self.preview, stretch=1)
        v.addLayout(btn_row)

        # First-render: populate the preview pane so the user
        # immediately sees the (anonymous) default payload.
        self._refresh_preview()

    # ----------------------------------------------------------- helpers
    def _build_payload(self) -> dict:
        """Construct the live payload from the current control state.

        Reads each opt-in checkbox + the institution edit + the note
        edit. The institution toggle uses the dialog's edit widget
        rather than the Setup-tab value because the user may have
        typed a contribution-specific string here.
        """
        from ..electrode_potential_history import export_anonymized_payload
        return export_anonymized_payload(
            user_name=self._user_name,
            user_email=self._user_email,
            institution=self.lbl_institution.text().strip(),
            session_name=self._session_name,
            include_user_name=self.cb_name.isChecked(),
            include_user_email=self.cb_email.isChecked(),
            include_institution=self.cb_institution.isChecked(),
            include_session_name=self.cb_session.isChecked(),
            note=self.note_input.toPlainText() or None,
        )

    def _refresh_preview(self, *_) -> None:
        """Re-render the JSON preview pane.

        Called from every input's change signal, so the preview
        always reflects what would be uploaded RIGHT NOW. We
        ``json.dumps`` with sort_keys + indent for readability;
        attribute fields appear at the top because they sort
        alphabetically before ``samples``.
        """
        payload = self._build_payload()
        self.preview.setPlainText(
            json.dumps(payload, indent=2, sort_keys=True))

    # ----------------------------------------------------------- actions
    @QtCore.pyqtSlot()
    def _on_open_github_issue(self) -> None:
        """Build the new-issue URL and open it in the user's browser.

        If the encoded body would exceed
        :data:`GITHUB_URL_BODY_BUDGET` we fall back to opening a
        bare new-issue page with a short stub body that asks the
        user to attach the JSON via the Save-to-file button — over-
        long URLs return a 414 from GitHub and are a confusing
        failure mode.
        """
        payload = self._build_payload()
        if not payload.get("samples"):
            QtWidgets.QMessageBox.information(
                self, "Nothing to contribute",
                "The local store has no samples yet. Run an "
                "experiment with E_ret captured before contributing.")
            return
        title = (
            f"Electrode data contribution — "
            f"{sum(len(v) for v in payload['samples'].values())} samples")
        body = build_issue_body(payload)
        if len(quote(body, safe="")) > GITHUB_URL_BODY_BUDGET:
            stub = (
                "## Electrode-potential data contribution\n\n"
                "The full payload is too large to fit in a URL. "
                "I'll attach the JSON file produced by the "
                "Save-to-file button in the contribute dialog.\n\n"
                "(PULSAR auto-generated stub.)")
            url = github_new_issue_url(
                self._repo_owner, self._repo_name,
                title=title, body=stub,
                labels=self.DEFAULT_LABELS,
            )
            QtWidgets.QMessageBox.information(
                self, "Payload too large for URL",
                "Your payload is too large to embed in a GitHub "
                "URL. I'm opening a stub issue — please use the "
                "Save-to-file button next, then drag-drop the "
                "saved .json onto the GitHub issue body before "
                "submitting.")
        else:
            url = github_new_issue_url(
                self._repo_owner, self._repo_name,
                title=title, body=body,
                labels=self.DEFAULT_LABELS,
            )
        # ``QDesktopServices.openUrl`` respects the user's default
        # browser. We do NOT use webbrowser.open() because that
        # bypasses Qt's URL-opening hooks (e.g. on Linux where the
        # WM may have a custom handler).
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Browser could not be opened",
                "Couldn't open your browser automatically. Copy the "
                f"URL below into your browser:\n\n{url}")

    @QtCore.pyqtSlot()
    def _on_save_to_file(self) -> None:
        """Save the live payload to a user-picked .json file.

        Useful when lab IT policy forbids posting on a public site
        directly from a workstation, or when the payload is too
        large to fit into the GitHub URL. The user attaches the
        file manually via whatever process their institution
        allows.
        """
        payload = self._build_payload()
        if not payload.get("samples"):
            QtWidgets.QMessageBox.information(
                self, "Nothing to save",
                "The local store has no samples yet. Run an "
                "experiment with E_ret captured before saving.")
            return
        suggested = "pulsar_electrode_data_contribution.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save contribution payload", suggested,
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            from pathlib import Path
            Path(path).write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Save failed",
                f"Could not write the JSON file:\n{path}\n\n{e}")
            return
        QtWidgets.QMessageBox.information(
            self, "Saved",
            f"Wrote the contribution payload to:\n{path}\n\n"
            "Attach it to a new GitHub Issue on "
            f"https://github.com/{self._repo_owner}/{self._repo_name}/issues "
            "(or the channel your institution prefers).")
