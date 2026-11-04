// Input validation for object-definition tabs (A1 = 表示ラベル).
// Wrapped in one namespace so none of its names collide with the other files in this project.
const InputValidation = (() => {
  const OBJECT_TAB_MARKER = '表示ラベル';  // A1 of every object-definition tab
  const HEADER_ROW = 10;
  const FIRST_ROW = 11;
  const FIELD_LIST_END = 'END[';  // column A marker closing the field list
  const LAST_COL = 25;  // column Y
  const OBJECT_LABEL_ROW = 5;
  const CREATE_TAB_LABEL = 'タブ作成';
  const SETUP_MARKER = 'inputValidationApplied';  // developer metadata key on each set-up tab

  // Column E values, lower-cased with spaces removed.
  const TYPES = {
    text: ['text'],
    encrypted: ['encryptedtext'],
    longText: ['longtextarea', 'richtextarea'],
    numeric: ['number', 'currency', 'percent', 'formulanumber', 'formulacurrency', 'formulapercent'],
    picklist: ['picklist', 'multiselectpicklist', '選択リスト', '複数選択リスト'],
    reference: ['lookup', 'masterdetail'],
    lookup: ['lookup'],
    rollup: ['rollupsummary'],
    autonumber: ['autonumber', '自動採番'],
    checkbox: ['checkbox', 'boolean'],
    uniqueAllowed: ['text', 'email', 'phone', 'url', 'number', 'currency', 'percent'],
  };

  // Each pattern is used both in the sheet formulas (RE2) and in JavaScript, so only the shared syntax is allowed.
  const TEXT_LEN_RE = '^([1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$';
  const ENCRYPTED_LEN_RE = '^([1-9]|[1-9][0-9]|1[0-6][0-9]|17[0-5])$';
  const NUMERIC_RE = '^ *([0-9]{1,2}) *([,，、] *([0-9]{1,2}) *)?$';
  const PICKLIST_ITEM = '[^;:；：,，、\\r\\n]+';
  // A line break separates picklist values like ';' does; each run of separators containing one becomes a single ';'.
  const PICKLIST_LINE_SEP_RE = '[ \\t]*([;\\r\\n][ \\t]*)*[\\r\\n]([ \\t]*[;\\r\\n])*[ \\t]*';
  const PICKLIST_RE = '^' + PICKLIST_ITEM + '(;' + PICKLIST_ITEM + ')*;?$|^' +
    PICKLIST_ITEM + ':' + PICKLIST_ITEM + '(;' + PICKLIST_ITEM + ':' + PICKLIST_ITEM + ')*;?$';
  // One {digits} number token (the digits may carry a starting number, e.g. {000010000}); other braces only date tokens.
  const AUTONUMBER_RE = '^([^{}]|\\{(YYYY|YY|MM|DD)\\})*\\{[0-9]{1,10}\\}([^{}]|\\{(YYYY|YY|MM|DD)\\})*$';
  const REVERSED_RE = '(^|;) *[A-Za-z0-9_]+ *:[^;]*[^ -~]';
  const OBJECT_API_RE = '^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*(__c)?$';
  const FIELD_API_RE = '^[A-Za-z][A-Za-z0-9]*((_[A-Za-z0-9]+)*__c)?$';
  const REL_NAME_RE = '^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$';
  const TRUTHY_RE = '^(true|y|yes|1|○|〇)$';
  const BOOL_RE = '^(true|false)$';
  const DELETE_RE = '^(setnull|restrict|cascade)$';
  const VISIBLE_LINES_RE = '^([1-9]|[1-4][0-9]|50)$';
  // Multi-line, case-insensitive: written as (?im) in formulas and with the 'im' flags in JavaScript.
  const ROLLUP_OP_RE = '^ *operation *[:：] *(count|sum|min|max) *$';
  const ROLLUP_COUNT_RE = '^ *operation *[:：] *count *$';
  const ROLLUP_CHILD_RE = '^ *child *[:：] *[a-z]';
  const ROLLUP_FIELD_RE = '^ *field *[:：] *[a-z]';

  const MSG = {
    type: '項目ラベル名がある場合はデータ型を選択\nSelect a data type when a label is entered',
    textLen: '文字数は 1〜255 の整数（例: 80）\nLength must be a whole number from 1 to 255, e.g. 80',
    encryptedLen: '暗号化テキストの文字数は 1〜175\nEncrypted text length must be 1 to 175',
    longLen: 'ロングテキスト・リッチテキストの文字数は 256〜131072\nLong / rich text length must be 256 to 131072',
    numeric: '「整数桁, 小数桁」で入力、合計18桁以内（例: 12, 3）\nEnter "digits, decimals", 18 in total at most, e.g. 12, 3',
    picklist: '選択リスト値は半角「;」または改行で区切る（例: 赤;緑 / 赤:Red;緑:Green）\nSeparate values with ; or a line break, e.g. Red;Green or Red:Red;Green:Green',
    reversed: '「ラベル:API名」の順で入力（例: 赤:Red）\nPut the label first, then the API name, e.g. 赤:Red',
    picklistDup: '選択リスト値のラベルまたはAPI名が重複\nPicklist labels and API names must be unique',
    reference: '参照先オブジェクトのAPI名を入力（例: Account, TI_Fnt_Deal__c）\nEnter the related object API name, e.g. Account or TI_Fnt_Deal__c',
    autonumber: '自動採番の表示形式は {0000} の番号を1つ含め、30文字以内（日付は {YYYY} {YY} {MM} {DD}、例: RCV-{0000000000}）\nAuto-number format needs one {0000} number (up to 10 digits), 30 chars max; dates {YYYY} {YY} {MM} {DD}, e.g. RCV-{0000000000}',
    rollup: '積み上げ集計は Operation: / Child: / Field: を行ごとに入力（COUNT は Field: 不要）\nRoll-up needs Operation:, Child: and Field: lines (no Field: for COUNT)',
    fieldApi: 'API参照名は英数字と _ のみ、40文字以内。末尾の __c 以外で _ を連続させない（例: TI_Fnt_Amount__c）\nAPI name: letters, digits and _, 40 chars max, no __ except the ending __c, e.g. TI_Fnt_Amount__c',
    fieldApiDup: 'API参照名が他の行と重複\nThis API name is already used on another row',
    labelDup: '項目ラベル名が他の行と重複\nThis field label is already used on another row',
    checkboxDefault: 'チェックボックスの初期値は TRUE または FALSE\nCheckbox default must be TRUE or FALSE',
    visibleLines: '表示行数は 1〜50 の整数（空欄なら 3）\nVisible lines: whole number 1-50 (blank = 3)',
    unique: 'ユニークはテキスト・数値・通貨・パーセント・メール・電話・URLのみ\nUnique is only allowed on Text, Number, Currency, Percent, Email, Phone or Url',
    externalId: '外部IDはテキスト・数値・通貨・パーセント・メール・電話・URLのみ\nExternal ID is only allowed on Text, Number, Currency, Percent, Email, Phone or Url',
    relName: '関係名は英数字と _ のみ、40文字以内\nRelationship name: letters, digits and _, 40 chars max',
    relNameDup: '関係名が他の行と重複\nThis relationship name is already used on another row',
    deleteConstraint: 'SetNull・Restrict・Cascade のいずれか（参照関係のみ）\nUse SetNull, Restrict or Cascade, on Lookup fields only',
    createTab: 'TRUE または FALSE を入力\nEnter TRUE or FALSE',
  };

  // Help text for columns with several rules; other columns show their rule messages.
  const COLUMN_HELP = {
    F: 'テキスト 1〜255 / 暗号化 1〜175 / ロング・リッチ 256〜131072 / 数値・通貨・% 「12, 3」\n' +
      'Text 1-255, Encrypted 1-175, Long / rich text 256-131072, Number / Currency / Percent "12, 3"',
    H: '選択リスト 赤;緑 または 赤:Red;緑:Green（改行区切りも可） / 参照・主従 オブジェクトAPI名 / 積み上げ集計 Operation: Child: Field: / 自動採番 RCV-{0000000000}\n' +
      'Picklist Red;Green or 赤:Red (one per line also works), Lookup the object API name, Roll-up Operation: / Child: / Field: lines, Auto-number RCV-{0000000000}',
  };

  // Sheet formulas, written for row FIRST_ROW; Sheets shifts them per row.
  const cell = c => '$' + c + FIRST_ROW;
  const txt = c => 'TO_TEXT(' + cell(c) + ')';
  const filled = c => 'LEN(TRIM(' + txt(c) + '))>0';
  const typeIs = key => 'OR(' + TYPES[key].map(t =>
    'LOWER(SUBSTITUTE(TRIM(' + cell('E') + ')," ",""))="' + t + '"').join(',') + ')';
  const matches = (c, re) => 'REGEXMATCH(' + txt(c) + ',"' + re + '")';
  const matchesLower = (c, re) => 'REGEXMATCH(LOWER(TRIM(' + txt(c) + ')),"' + re + '")';
  const matchesLines = (c, re) => 'REGEXMATCH(' + txt(c) + ',"(?im)' + re + '")';
  const maxLen = (c, n) => 'LEN(' + txt(c) + ')<=' + n;
  const dupInColumn = (c, last) => 'COUNTIF($' + c + '$' + FIRST_ROW + ':$' + c + '$' + last + ',' + cell(c) + ')>1';
  // Labels are compared ignoring case, spaces and full-width / half-width letters, digits and brackets.
  const labelKey = s => 'LOWER(SUBSTITUTE(SUBSTITUTE(ASC(' + s + '&"")," ",""),"　",""))';
  const labelDupInColumn = (c, last) => 'SUMPRODUCT(--(' + labelKey('$' + c + '$' + FIRST_ROW + ':$' + c + '$' + last) +
    '=' + labelKey(cell(c)) + '))>1';
  const hasDupItems = s => 'COUNTA(SPLIT(' + s + ',";"))<>COUNTUNIQUE(SPLIT(' + s + ',";"))';

  // The same checks on a row of display values; isBad() must agree with bad so the note and the highlight match.
  const get = (row, c) => String(row[colIndex(c) - 1]);
  const has = (row, c) => get(row, c).trim() !== '';
  const isType = (row, key) => TYPES[key].includes(get(row, 'E').trim().replace(/ /g, '').toLowerCase());
  const test = (row, c, re) => new RegExp(re).test(get(row, c));
  const testLower = (row, c, re) => new RegExp(re).test(get(row, c).trim().toLowerCase());
  const testLines = (row, c, re) => new RegExp(re, 'im').test(get(row, c));
  const dupIn = (rows, row, c) => rows.filter(r => get(r, c).toLowerCase() === get(row, c).toLowerCase()).length > 1;
  const toLabelKey = s => s.replace(/[\uFF01-\uFF5E]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0))
    .replace(/[ \u3000]/g, '').toLowerCase();
  const labelDupIn = (rows, row, c) => rows.filter(r => toLabelKey(get(r, c)) === toLabelKey(get(row, c))).length > 1;
  const hasDupList = s => {
    const items = s.split(';').filter(x => x !== '');
    return new Set(items).size !== items.length;
  };

  function numericSizeOk(f) {
    const m = f.match(new RegExp(NUMERIC_RE));
    if (!m) return false;
    const scale = m[3] ? Number(m[3]) : 0;
    const total = Number(m[1]) + scale;
    return total >= 1 && total <= 18 && scale <= 17;
  }

  const numericDigits = 'VALUE(REGEXEXTRACT(' + txt('F') + ',"^ *([0-9]+)"))';
  const numericScale = 'IFERROR(VALUE(REGEXEXTRACT(' + txt('F') + ',"[,，、] *([0-9]+)")),0)';
  const numericTotal = '(' + numericDigits + '+' + numericScale + ')';
  const numericOk = 'IFERROR(AND(' + matches('F', NUMERIC_RE) + ',' + numericTotal + '>=1,' +
    numericTotal + '<=18,' + numericScale + '<=17),FALSE)';

  const picklistText = 'REGEXREPLACE(REGEXREPLACE(' + txt('H') + ',"' + PICKLIST_LINE_SEP_RE + '",";"),"^;","")';
  const picklistLabels = 'REGEXREPLACE(LOWER(' + picklistText + '),":[^;]*","")';
  const picklistApis = 'REGEXREPLACE(LOWER(' + picklistText + '),"(^|;)[^;:]*:","$1")';
  const picklistOf = row => get(row, 'H').replace(new RegExp(PICKLIST_LINE_SEP_RE, 'g'), ';').replace(/^;/, '');

  const RULES = [
    {
      col: 'C', message: MSG.labelDup,
      bad: last => 'AND(' + filled('C') + ',' + labelDupInColumn('C', last) + ')',
      isBad: (r, rows) => has(r, 'C') && labelDupIn(rows, r, 'C'),
    },
    {
      col: 'E', message: MSG.type,
      bad: 'AND(' + filled('C') + ',NOT(' + filled('E') + '))',
      isBad: r => has(r, 'C') && !has(r, 'E'),
    },
    {
      col: 'F', message: MSG.textLen,
      bad: 'AND(' + typeIs('text') + ',NOT(' + matches('F', TEXT_LEN_RE) + '))',
      isBad: r => isType(r, 'text') && !test(r, 'F', TEXT_LEN_RE),
    },
    {
      col: 'F', message: MSG.encryptedLen,
      bad: 'AND(' + typeIs('encrypted') + ',NOT(' + matches('F', ENCRYPTED_LEN_RE) + '))',
      isBad: r => isType(r, 'encrypted') && !test(r, 'F', ENCRYPTED_LEN_RE),
    },
    {
      col: 'F', message: MSG.longLen,
      bad: 'AND(' + typeIs('longText') + ',NOT(IFERROR(AND(' + matches('F', '^[0-9]+$') + ',VALUE(' +
        txt('F') + ')>=256,VALUE(' + txt('F') + ')<=131072),FALSE)))',
      isBad: r => {
        const f = get(r, 'F');
        return isType(r, 'longText') && !(/^[0-9]+$/.test(f) && Number(f) >= 256 && Number(f) <= 131072);
      },
    },
    {
      col: 'F', message: MSG.numeric,
      bad: 'AND(' + typeIs('numeric') + ',NOT(' + numericOk + '))',
      isBad: r => isType(r, 'numeric') && !numericSizeOk(get(r, 'F')),
    },
    {
      col: 'H', message: MSG.picklist,
      bad: 'AND(' + typeIs('picklist') + ',NOT(REGEXMATCH(' + picklistText + ',"' + PICKLIST_RE + '")))',
      isBad: r => isType(r, 'picklist') && !new RegExp(PICKLIST_RE).test(picklistOf(r)),
    },
    {
      col: 'H', message: MSG.reversed,
      bad: 'AND(' + typeIs('picklist') + ',REGEXMATCH(' + picklistText + ',"' + REVERSED_RE + '"))',
      isBad: r => isType(r, 'picklist') && new RegExp(REVERSED_RE).test(picklistOf(r)),
    },
    {
      col: 'H', message: MSG.picklistDup,
      bad: 'AND(' + typeIs('picklist') + ',' + filled('H') + ',IFERROR(OR(' + hasDupItems(picklistLabels) + ',' +
        hasDupItems(picklistApis) + '),FALSE))',
      isBad: r => {
        const h = picklistOf(r).toLowerCase();
        return isType(r, 'picklist') && h.trim() !== '' &&
          (hasDupList(h.replace(/:[^;]*/g, '')) || hasDupList(h.replace(/(^|;)[^;:]*:/g, '$1')));
      },
    },
    {
      col: 'H', message: MSG.reference,
      bad: 'AND(' + typeIs('reference') + ',NOT(' + matches('H', OBJECT_API_RE) + '))',
      isBad: r => isType(r, 'reference') && !test(r, 'H', OBJECT_API_RE),
    },
    {
      col: 'H', message: MSG.autonumber,
      bad: 'AND(' + typeIs('autonumber') + ',NOT(AND(' + matches('H', AUTONUMBER_RE) + ',' + maxLen('H', 30) + ')))',
      isBad: r => isType(r, 'autonumber') && !(test(r, 'H', AUTONUMBER_RE) && get(r, 'H').length <= 30),
    },
    {
      col: 'H', message: MSG.rollup,
      bad: 'AND(' + typeIs('rollup') + ',NOT(AND(' + matchesLines('H', ROLLUP_OP_RE) + ',' +
        matchesLines('H', ROLLUP_CHILD_RE) + ',OR(' + matchesLines('H', ROLLUP_COUNT_RE) + ',' +
        matchesLines('H', ROLLUP_FIELD_RE) + '))))',
      isBad: r => isType(r, 'rollup') && !(testLines(r, 'H', ROLLUP_OP_RE) && testLines(r, 'H', ROLLUP_CHILD_RE) &&
        (testLines(r, 'H', ROLLUP_COUNT_RE) || testLines(r, 'H', ROLLUP_FIELD_RE))),
    },
    {
      col: 'D', message: MSG.fieldApi,
      bad: 'AND(' + filled('D') + ',NOT(AND(' + matches('D', FIELD_API_RE) + ',' + maxLen('D', 40) + ')))',
      isBad: r => has(r, 'D') && !(test(r, 'D', FIELD_API_RE) && get(r, 'D').length <= 40),
    },
    {
      col: 'D', message: MSG.fieldApiDup,
      bad: last => 'AND(' + filled('D') + ',' + dupInColumn('D', last) + ')',
      isBad: (r, rows) => has(r, 'D') && dupIn(rows, r, 'D'),
    },
    {
      col: 'L', message: MSG.checkboxDefault,
      bad: 'AND(' + typeIs('checkbox') + ',' + filled('L') + ',NOT(' + matchesLower('L', BOOL_RE) + '))',
      isBad: r => isType(r, 'checkbox') && has(r, 'L') && !testLower(r, 'L', BOOL_RE),
    },
    {
      col: 'S', message: MSG.visibleLines,
      bad: 'AND(' + typeIs('longText') + ',' + filled('S') + ',NOT(' + matches('S', VISIBLE_LINES_RE) + '))',
      isBad: r => isType(r, 'longText') && has(r, 'S') && !test(r, 'S', VISIBLE_LINES_RE),
    },
    {
      col: 'J', message: MSG.unique,
      bad: 'AND(' + matchesLower('J', TRUTHY_RE) + ',NOT(' + typeIs('uniqueAllowed') + '))',
      isBad: r => testLower(r, 'J', TRUTHY_RE) && !isType(r, 'uniqueAllowed'),
    },
    {
      col: 'K', message: MSG.externalId,
      bad: 'AND(' + matchesLower('K', TRUTHY_RE) + ',NOT(' + typeIs('uniqueAllowed') + '))',
      isBad: r => testLower(r, 'K', TRUTHY_RE) && !isType(r, 'uniqueAllowed'),
    },
    {
      col: 'X', message: MSG.relName,
      bad: 'AND(' + filled('X') + ',NOT(AND(' + matches('X', REL_NAME_RE) + ',' + maxLen('X', 40) + ')))',
      isBad: r => has(r, 'X') && !(test(r, 'X', REL_NAME_RE) && get(r, 'X').length <= 40),
    },
    {
      col: 'X', message: MSG.relNameDup,
      bad: last => 'AND(' + filled('X') + ',' + dupInColumn('X', last) + ')',
      isBad: (r, rows) => has(r, 'X') && dupIn(rows, r, 'X'),
    },
    {
      col: 'Y', message: MSG.deleteConstraint,
      bad: 'AND(' + filled('Y') + ',OR(NOT(' + matchesLower('Y', DELETE_RE) + '),NOT(' + typeIs('lookup') + ')))',
      isBad: r => has(r, 'Y') && (!testLower(r, 'Y', DELETE_RE) || !isType(r, 'lookup')),
    },
  ];

  // Earlier wordings, so notes written with them are still recognised and replaced.
  const RETIRED_MESSAGES = [
    'API参照名は英数字と _ のみ、40文字以内（例: TI_Fnt_Amount__c）\nAPI name: letters, digits and _, 40 chars max, e.g. TI_Fnt_Amount__c',
    '選択リスト値は半角「;」区切り（例: 赤;緑 / 赤:Red;緑:Green）\nSeparate values with ; e.g. Red;Green or Red:Red;Green:Green',
  ];
  const ALL_MESSAGES = new Set(Object.values(MSG).concat(RETIRED_MESSAGES));

  function colIndex(letter) {
    return letter.charCodeAt(0) - 64;
  }

  function rulesByColumn() {
    const cols = {};
    RULES.forEach(rule => (cols[rule.col] = cols[rule.col] || []).push(rule));
    return cols;
  }

  function createTabCell(sh) {
    const labels = sh.getRange(OBJECT_LABEL_ROW, 1, 1, sh.getLastColumn()).getDisplayValues()[0];
    const i = labels.findIndex(v => v.includes(CREATE_TAB_LABEL));
    return i < 0 ? null : sh.getRange(OBJECT_LABEL_ROW + 1, i + 1);
  }

  function fieldLastRow(sh) {
    if (sh.getLastRow() < FIRST_ROW) return FIRST_ROW - 1;
    const colA = sh.getRange(FIRST_ROW, 1, sh.getLastRow() - FIRST_ROW + 1, 1).getDisplayValues();
    const i = colA.findIndex(([v]) => v.startsWith(FIELD_LIST_END));
    return i < 0 ? sh.getLastRow() : FIRST_ROW + i - 1;
  }

  const badFormula = (rule, last) => typeof rule.bad === 'function' ? rule.bad(last) : rule.bad;

  // Row-10 header each rule column must carry; a tab laid out differently is skipped.
  const EXPECTED_HEADERS = {
    C: 'label', D: 'fullName', E: 'type', F: 'length', H: 'value', J: 'unique', K: 'externalId',
    L: 'defaultValue', S: 'visibleLines', X: 'relationshipName', Y: 'deleteConstraint',
  };

  function isObjectTab(sh) {
    return sh.getRange('A1').getDisplayValue().trim() === OBJECT_TAB_MARKER;
  }

  function headerMismatches(sh) {
    const headers = sh.getRange(HEADER_ROW, 1, 1, LAST_COL).getDisplayValues()[0];
    return Object.keys(EXPECTED_HEADERS)
      .filter(col => headers[colIndex(col) - 1].trim() !== EXPECTED_HEADERS[col])
      .map(col => col + '10="' + headers[colIndex(col) - 1] + '" (expected ' + EXPECTED_HEADERS[col] + ')');
  }

  function applyToSheet(sh) {
    const last = fieldLastRow(sh);
    if (last < FIRST_ROW) return;
    const cols = rulesByColumn();
    const targets = Object.keys(cols).map(col => ({
      range: sh.getRange(col + FIRST_ROW + ':' + col + last),
      bad: cols[col].map(rule => badFormula(rule, last)),
      help: COLUMN_HELP[col] || cols[col].map(rule => rule.message).join('\n'),
      keepDropdown: col === 'E',
    }));
    const tabCell = createTabCell(sh);
    if (tabCell) {
      targets.push({
        range: tabCell,
        bad: ['NOT(REGEXMATCH(LOWER(TRIM(TO_TEXT(' + tabCell.getA1Notation() + '))),"' + BOOL_RE + '"))'],
        help: MSG.createTab,
      });
    }

    // Matched by start cell, so a rule left on a shorter range by an earlier run is replaced too.
    const managed = targets.map(t => t.range.getCell(1, 1).getA1Notation());
    const kept = sh.getConditionalFormatRules().filter(r =>
      !r.getRanges().some(g => g.getNumColumns() === 1 && managed.includes(g.getCell(1, 1).getA1Notation())));
    // Data validation skips empty cells, so the highlight also covers a blank cell that must be filled.
    const highlights = targets.map(t => SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=OR(' + t.bad.join(',') + ')')
      .setBackground('#F4CCCC')
      .setFontColor('#990000')
      .setRanges([t.range])
      .build());
    sh.setConditionalFormatRules(kept.concat(highlights));

    targets.forEach(t => {
      if (t.keepDropdown) {
        const dropdown = t.range.getDataValidations().map(([v]) => v).find(v => v);
        if (dropdown) t.range.setDataValidation(dropdown.copy().setHelpText(t.help).build());
        return;
      }
      t.range.setDataValidation(SpreadsheetApp.newDataValidation()
        .requireFormulaSatisfied('=NOT(OR(' + t.bad.join(',') + '))')
        .setAllowInvalid(true)  // warning triangle; set false to reject input
        .setHelpText(t.help)
        .build());
    });

    syncNotes(sh);
  }

  // Notes list every rule a non-blank cell breaks and are removed once it is valid.
  // Blank cells are left to the help text, which Sheets shows only on cells it has not flagged.
  // Notes written by anyone else are never overwritten or removed.
  function syncNotes(sh) {
    const last = fieldLastRow(sh);
    if (last < FIRST_ROW) return;
    const rows = sh.getRange(FIRST_ROW, 1, last - FIRST_ROW + 1, LAST_COL).getDisplayValues();
    const cols = rulesByColumn();
    Object.keys(cols).forEach(col => {
      const range = sh.getRange(col + FIRST_ROW + ':' + col + last);
      writeNotes(range, rows.map(row => has(row, col)
        ? cols[col].filter(rule => rule.isBad(row, rows)).map(rule => rule.message)
        : []));
    });

    const tabCell = createTabCell(sh);
    if (tabCell) {
      const v = tabCell.getDisplayValue().trim().toLowerCase();
      writeNotes(tabCell, [v !== '' && !new RegExp(BOOL_RE).test(v) ? [MSG.createTab] : []]);
    }
  }

  function writeNotes(range, messages) {
    const notes = range.getNotes();
    let changed = false;
    const updated = notes.map(([note], i) => {
      if (note && !note.split('\n\n').every(part => ALL_MESSAGES.has(part))) return [note];
      const next = messages[i].join('\n\n');
      if (next !== note) changed = true;
      return [next];
    });
    if (changed) range.setNotes(updated);
  }

  // Tabs set up before the marker existed, or copied from a set-up tab, are recognised by the column D highlight.
  function isSetUp(sh) {
    if (sh.getDeveloperMetadata().some(m => m.getKey() === SETUP_MARKER)) return true;
    const start = 'D' + FIRST_ROW;
    const found = sh.getConditionalFormatRules().some(r =>
      r.getRanges().some(g => g.getNumColumns() === 1 && g.getCell(1, 1).getA1Notation() === start));
    if (found) markSetUp(sh);
    return found;
  }

  function markSetUp(sh) {
    if (!sh.getDeveloperMetadata().some(m => m.getKey() === SETUP_MARKER)) sh.addDeveloperMetadata(SETUP_MARKER);
  }

  // A new object tab is set up on its first edit once A1 reads 表示ラベル and row 10 has the standard headers.
  function handleEdit(e) {
    const sh = e.range.getSheet();
    if (e.range.getLastRow() < OBJECT_LABEL_ROW || !isObjectTab(sh)) return;
    if (headerMismatches(sh).length) return;
    if (!isSetUp(sh)) {
      applyToSheet(sh);
      markSetUp(sh);
      return;
    }
    if (e.range.getRow() > fieldLastRow(sh)) return;
    syncNotes(sh);
  }

  // Applies tab by tab within the 30-minute run limit; run again to continue where it stopped.
  function applyAll() {
    const props = PropertiesService.getScriptProperties();
    const done = new Set(JSON.parse(props.getProperty(PROGRESS_KEY) || '[]'));
    const started = Date.now();
    const pending = SpreadsheetApp.getActive().getSheets().filter(sh => isObjectTab(sh) && !done.has(sh.getSheetId()));
    for (const sh of pending) {
      if (Date.now() - started > TIME_BUDGET_MS) break;
      const mismatches = headerMismatches(sh);
      if (mismatches.length) Logger.log('Skipped ' + sh.getName() + ': ' + mismatches.join(', '));
      else {
        applyToSheet(sh);
        markSetUp(sh);
        Logger.log('Applied ' + sh.getName() + ' (rows ' + FIRST_ROW + '-' + fieldLastRow(sh) + ')');
      }
      done.add(sh.getSheetId());
      props.setProperty(PROGRESS_KEY, JSON.stringify([...done]));
    }
    const left = pending.filter(sh => !done.has(sh.getSheetId())).length;
    if (left) Logger.log(left + ' tab(s) left: run applyInputValidations again.');
    else {
      props.deleteProperty(PROGRESS_KEY);
      Logger.log('All object tabs done.');
    }
  }

  return { applyAll, handleEdit };
})();

const PROGRESS_KEY = 'INPUT_VALIDATION_DONE_SHEETS';
const TIME_BUDGET_MS = 25 * 60 * 1000;

function applyInputValidations() {
  InputValidation.applyAll();
}

// Called from onEdit in Common.gs.
function inputValidationOnEdit_(e) {
  InputValidation.handleEdit(e);
}
