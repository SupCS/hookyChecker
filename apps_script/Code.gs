/**
 * Hooky Checker sender.
 *
 * 1. Replace API_URL and INGEST_TOKEN.
 * 2. Run testHookyConnection once and approve access.
 * 3. Run installDailyTrigger once.
 */
const API_URL = 'PASTE_API_URL_HERE/api/v1/snapshots';
const INGEST_TOKEN = 'PASTE_INGEST_TOKEN_HERE';
const WORKSHEET_NAME = 'All_Data';

function sendHookySnapshot() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(WORKSHEET_NAME);
  if (!sheet) {
    throw new Error(`Worksheet "${WORKSHEET_NAME}" not found`);
  }

  const values = sheet.getDataRange().getValues().map(row =>
    row.map(value => value instanceof Date ? value.toISOString() : value)
  );

  const response = UrlFetchApp.fetch(API_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: `Bearer ${INGEST_TOKEN}` },
    payload: JSON.stringify({ values }),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error(`Hooky Checker returned ${code}: ${response.getContentText()}`);
  }
  console.log(response.getContentText());
}

function testHookyConnection() {
  sendHookySnapshot();
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(trigger => trigger.getHandlerFunction() === 'sendHookySnapshot')
    .forEach(trigger => ScriptApp.deleteTrigger(trigger));

  ScriptApp.newTrigger('sendHookySnapshot')
    .timeBased()
    .everyDays(1)
    .atHour(8)
    .create();
}
