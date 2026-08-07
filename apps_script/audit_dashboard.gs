/**
 * Audit Dashboard — Apps Script Web App
 *
 * Accepts POST requests from bots and writes to two tabs:
 *   - "Runs" tab: one row per bot run (summary)
 *   - "Details" tab: one row per member processed
 *
 * Deploy: Extensions > Apps Script > Deploy > Web app
 *   - Execute as: Me
 *   - Who has access: Anyone (or Anyone with link)
 *
 * POST body format:
 *   { "type": "run", "run_id": "...", "date": "...", ... }
 *   { "type": "detail", "rows": [ {...}, {...} ] }
 */

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet();
  var data = JSON.parse(e.postData.contents);

  if (data.type === "run") {
    var runsTab = sheet.getSheetByName("Runs");
    if (!runsTab) {
      runsTab = sheet.insertSheet("Runs");
      runsTab.appendRow([
        "Run ID", "Date", "Time (UTC)", "Bot Type", "Trigger",
        "Status", "Total Processed", "Successful", "Failed", "Duration (sec)"
      ]);
      runsTab.getRange("1:1").setFontWeight("bold");
    }
    runsTab.appendRow([
      data.run_id,
      data.date,
      data.time_utc,
      data.bot_type,
      data.trigger,
      data.status,
      data.total_processed,
      data.successful,
      data.failed,
      data.duration_sec
    ]);

  } else if (data.type === "detail") {
    var detailsTab = sheet.getSheetByName("Details");
    if (!detailsTab) {
      detailsTab = sheet.insertSheet("Details");
      detailsTab.appendRow([
        "Run ID", "Date", "Bot Type", "Ticket", "MID", "Name",
        "Result", "Failure Reason", "Manual Review", "Review Notes"
      ]);
      detailsTab.getRange("1:1").setFontWeight("bold");
      // Add data validation dropdown on Manual Review column (column I)
      var rule = SpreadsheetApp.newDataValidation()
        .requireValueInList(["Pending", "Reviewed", "Escalated", "Resolved"], true)
        .setAllowInvalid(true)
        .build();
      detailsTab.getRange("I2:I1000").setDataValidation(rule);
    }

    var rows = data.rows || [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      detailsTab.appendRow([
        r.run_id,
        r.date,
        r.bot_type,
        r.ticket,
        r.mid,
        r.name,
        r.result,
        r.failure_reason || "",
        r.manual_review || "Pending",
        ""
      ]);
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify({ "status": "ok" }))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({ "status": "ready", "message": "Audit Dashboard API is live. Use POST to submit data." }))
    .setMimeType(ContentService.MimeType.JSON);
}
