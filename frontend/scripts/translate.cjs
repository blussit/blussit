const fs = require('fs');
const path = require('path');

const i18nReplacements = {
  '"Your jobs"': 't("captain.jobs.title")',
  '"Manage your assigned wash jobs."': 't("captain.jobs.description")',
  '"Filters"': 't("captain.jobs.filters")',
  '"No jobs found"': 't("captain.jobs.empty.title")',
  '"You have no assigned jobs at the moment."': 't("captain.jobs.empty.desc")',
  '"All statuses"': 't("captain.status.all")',
  '"Active"': 't("captain.status.active")',
  '"Assigned"': 't("captain.status.assigned")',
  '"On the way"': 't("captain.status.captain_on_the_way")',
  '"In progress"': 't("captain.status.service_started")',
  '"Completed"': 't("captain.status.completed")',
  '"Cancelled"': 't("captain.status.cancelled")',
  '"Payment pending"': 't("captain.status.payment_pending")',
  '"Start Job"': 't("captain.actions.startJob")',
  '"Head out"': 't("captain.actions.headOut")',
  '"Reach & verify"': 't("captain.actions.reachVerify")',
  '"Before photo"': 't("captain.actions.beforePhoto")',
  '"After photo"': 't("captain.actions.afterPhoto")',
  '"Complete Job"': 't("captain.actions.completeJob")',
  '"Collect Payment"': 't("captain.actions.collectPayment")',
  '"View details"': 't("captain.actions.viewDetails")',
  '"Customer"': 't("captain.job.customer")',
  '"Vehicle"': 't("captain.job.vehicle")',
  '"Scheduled"': 't("captain.job.scheduled")',
  '"Location"': 't("captain.job.location")',
  '"Next action"': 't("captain.job.nextAction")',
  '"Attendance"': 't("captain.attendance.title")',
  '"View your daily attendance and log."': 't("captain.attendance.description")',
  '"Profile"': 't("captain.profile.title")',
  '"Manage your captain profile."': 't("captain.profile.description")',
  '"Loading..."': 't("captain.common.loading")',
  '"An error occurred"': 't("captain.common.error")',
  '"Save"': 't("captain.common.save")',
  '"Cancel"': 't("captain.common.cancel")',
  '"Submit"': 't("captain.common.submit")',
  '"Confirm"': 't("captain.common.confirm")',
  '"Close"': 't("captain.common.close")',
  '"Status"': 't("captain.common.status")',
  '"Head to location?"': 't("captain.modal.headOut.title")',
  '"Confirm you are on the way."': 't("captain.modal.headOut.desc")',
  '"Verify Vehicle"': 't("captain.modal.verify.title")',
  '"Enter the last 4 digits of the registration."': 't("captain.modal.verify.desc")',
  '"e.g. 1234"': 't("captain.modal.verify.placeholder")',
  '"Take Before Photo"': 't("captain.modal.photo.before")',
  '"Take After Photo"': 't("captain.modal.photo.after")',
  '"Report Risk"': 't("captain.modal.reportRisk.title")',
  '"Cancel Job"': 't("captain.modal.cancel.title")'
};

const dirs = ['src/pages/captain', 'src/components/captain'];

function processFile(filePath) {
  let content = fs.readFileSync(filePath, 'utf8');
  let original = content;

  // Ensure import useCaptainTranslation is there if we make replacements
  let madeChanges = false;

  for (const [key, value] of Object.entries(i18nReplacements)) {
    const keyString = key;
    // Replace text in JSX like >Your jobs< to >{t("captain.jobs.title")}<
    const textKey = key.slice(1, -1);
    const regexJsx = new RegExp(`>\\s*${textKey}\\s*<`, 'g');
    if (regexJsx.test(content)) {
      content = content.replace(regexJsx, `>{${value}}<`);
      madeChanges = true;
    }

    // Replace strings like title="Your jobs" to title={t("captain.jobs.title")}
    const regexProp = new RegExp(`="${textKey}"`, 'g');
    if (regexProp.test(content)) {
      content = content.replace(regexProp, `={${value}}`);
      madeChanges = true;
    }
    
    // Replace standalone strings in js
    if (content.includes(keyString)) {
      content = content.split(keyString).join(value);
      madeChanges = true;
    }
  }

  if (madeChanges && original !== content) {
    if (!content.includes('useCaptainTranslation')) {
      content = `import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";\n` + content;
      // also inject const { t } = useCaptainTranslation(); into the component body?
      // Since it's tricky to inject exactly at the top of the component via regex perfectly,
      // I'll just write the file and let me manually inject `const { t } = useCaptainTranslation();` using replace_file_content or manually fixing linter errors.
    }
    fs.writeFileSync(filePath, content, 'utf8');
    console.log(`Updated ${filePath}`);
  }
}

dirs.forEach(dir => {
  const files = fs.readdirSync(dir);
  files.forEach(file => {
    if (file.endsWith('.tsx') || file.endsWith('.ts')) {
      processFile(path.join(dir, file));
    }
  });
});
