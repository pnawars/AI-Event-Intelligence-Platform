/**
 * scheduler/index.js
 * Main entry point. Runs Agent 1 then Agent 2 on a cron schedule.
 * Default: 07:00 every day (configurable via CRON_SCHEDULE in .env)
 *
 * Start with: npm start
 */
import 'dotenv/config';
import cron from 'node-cron';
import { runAgent1 } from '../agents/agent1_eventFinder.js';
import { runAgent2 } from '../agents/agent2_companyResearcher.js';

const CRON_SCHEDULE = process.env.CRON_SCHEDULE || '0 7 * * *';

async function runPipeline() {
  const runStart = new Date();
  console.log('\n' + '═'.repeat(60));
  console.log(`🚀 Event Intelligence Pipeline started at ${runStart.toISOString()}`);
  console.log('═'.repeat(60));

  try {
    // Step 1: Find new events
    const agent1Result = await runAgent1();

    // Step 2: Research companies for those events
    const agent2Result = await runAgent2();

    const duration = ((Date.now() - runStart.getTime()) / 1000).toFixed(1);
    console.log('\n' + '═'.repeat(60));
    console.log(`✅ Pipeline complete in ${duration}s`);
    console.log(`   Events: ${agent1Result.inserted} new, ${agent1Result.updated} updated`);
    console.log(`   Companies: ${agent2Result.companies} added`);
    console.log('═'.repeat(60) + '\n');

  } catch (err) {
    console.error('\n❌ Pipeline error:', err);
  }
}

// ─── Cron schedule ────────────────────────────────────────────────────────────
console.log(`⏰ Event Intelligence Scheduler started`);
console.log(`   Schedule: "${CRON_SCHEDULE}" (${describeSchedule(CRON_SCHEDULE)})`);
console.log(`   Next run: ${getNextRunTime(CRON_SCHEDULE)}`);
console.log(`\n   Run "npm run run-once" to trigger immediately.\n`);

cron.schedule(CRON_SCHEDULE, runPipeline, {
  scheduled: true,
  timezone: 'Europe/London',
});

function describeSchedule(expr) {
  if (expr === '0 7 * * *')  return 'Every day at 07:00 London time';
  if (expr === '0 */6 * * *') return 'Every 6 hours';
  if (expr === '*/30 * * * *') return 'Every 30 minutes';
  return expr;
}

function getNextRunTime(expr) {
  // Simple display only — just show "tomorrow 07:00" for default
  if (expr === '0 7 * * *') {
    const next = new Date();
    next.setDate(next.getDate() + 1);
    next.setHours(7, 0, 0, 0);
    return next.toLocaleString('en-GB', { timeZone: 'Europe/London' });
  }
  return 'See cron expression';
}
