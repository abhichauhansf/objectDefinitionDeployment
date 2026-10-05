/**
 * extend_type_dropdown.gs — add data-type values to the column-E (データ型 / type)
 * dropdown on the Format tab and every object tab of the Toray DD workbook.
 *
 * Add this as a NEW file in the workbook's Apps Script project (do not replace
 * existing files), then run `previewTypeDropdown` for a no-write report and
 * `extendTypeDropdown` to apply.
 *
 * Idempotent: values already present are skipped, and only cells that ALREADY
 * carry the type dropdown are touched, so each tab's validation range, its
 * "reject input" setting and its help text are preserved.
 *
 * Every name below is prefixed/specific so it cannot collide with the globals
 * of other scripts in the same project (Apps Script shares one global scope).
 */

function previewTypeDropdown() {
  Logger.log(tiFntTypeDropdown_(false));
}

function extendTypeDropdown() {
  Logger.log(tiFntTypeDropdown_(true));
}

/**
 * Repair pass for cells the main run left on the old list. Verified against the
 * workbook on 2026-09-15: 29 cells on `Deal` kept the 26-value list while the
 * other 2,349 cells were updated. This copies the already-correct rule from a
 * good cell in the same column onto those cells, and logs each one's existing
 * criteria type so the cause is visible.
 */
function repairTypeDropdown() {
  var tab = 'Deal';
  var rows = [18, 19, 115, 132, 133, 134, 135, 136, 137, 138, 139, 140, 142, 144, 145,
              151, 152, 153, 154, 155, 166, 167, 168, 169, 170, 182, 183, 190, 196];
  var typeCol = 5;
  var newValues = ['Rollup summary', 'Text Area', 'Rich Text Area', 'Time'];

  var sheet = SpreadsheetApp.getActive().getSheetByName(tab);
  if (!sheet) { Logger.log('tab not found: ' + tab); return; }

  // Find a cell in column E whose rule already carries the new values — that is
  // the canonical rule to copy (keeps strictness, help text and dropdown flag).
  var probe = sheet.getRange(11, typeCol, sheet.getMaxRows() - 10, 1).getDataValidations();
  var canonical = null;
  for (var i = 0; i < probe.length && !canonical; i++) {
    var r = probe[i][0];
    if (!r || r.getCriteriaType() !== SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) continue;
    var vals = r.getCriteriaValues()[0];
    var complete = newValues.every(function (v) { return vals.indexOf(v) !== -1; });
    if (complete) canonical = r;
  }
  if (!canonical) { Logger.log('no updated rule found on ' + tab + ' to copy from'); return; }

  var lines = [];
  rows.forEach(function (row) {
    var cell = sheet.getRange(row, typeCol);
    var old = cell.getDataValidation();
    lines.push('  E' + row + ': was ' +
      (old ? old.getCriteriaType() + ' with ' +
        (old.getCriteriaValues()[0] && old.getCriteriaValues()[0].length) + ' values'
           : 'NO VALIDATION'));
    cell.setDataValidation(canonical);
  });

  Logger.log('REPAIRED ' + rows.length + ' cells on ' + tab + '\n' + lines.join('\n'));
}

function tiFntTypeDropdown_(apply) {
  var newValues = ['Rollup summary', 'Text Area', 'Rich Text Area', 'Time'];
  var typeCol = 5; // column E
  var firstRow = 11; // field rows start at 11 on every object tab

  var inList = SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST;
  var lines = [];
  var totalCells = 0;
  var totalTabs = 0;

  SpreadsheetApp.getActive().getSheets().forEach(function (sheet) {
    var maxRow = sheet.getMaxRows();
    if (maxRow < firstRow) return;

    var range = sheet.getRange(firstRow, typeCol, maxRow - firstRow + 1, 1);
    var rules = range.getDataValidations();
    var changed = 0;
    var from = 0;
    var to = 0;

    for (var i = 0; i < rules.length; i++) {
      var rule = rules[i][0];
      if (!rule || rule.getCriteriaType() !== inList) continue;

      var args = rule.getCriteriaValues();
      var values = args[0].slice();
      var showDropdown = args.length > 1 ? args[1] : true;
      var missing = newValues.filter(function (v) { return values.indexOf(v) === -1; });
      if (!missing.length) continue;

      rules[i][0] = rule.copy().requireValueInList(values.concat(missing), showDropdown).build();
      changed++;
      if (!from) from = firstRow + i;
      to = firstRow + i;
    }

    if (!changed) return;
    if (apply) range.setDataValidations(rules);
    totalTabs++;
    totalCells += changed;
    lines.push(sheet.getName() + ': E' + from + ':E' + to + ' (' + changed + ' cells)');
  });

  var head = (apply ? 'APPLIED' : 'PREVIEW') + ' — added ' + newValues.join(', ') +
    ' to ' + totalCells + ' cells across ' + totalTabs + ' tabs';
  return [head].concat(lines).join('\n');
}
