// Only for workbooks that have no other onEdit (e.g. the demo sheets).
// In OrgSync, Common.gs onEdit calls inputValidationOnEdit_ instead.
function onEdit(e) {
  try {
    inputValidationOnEdit_(e);
  } catch (err) {
    console.error(err);
  }
}
