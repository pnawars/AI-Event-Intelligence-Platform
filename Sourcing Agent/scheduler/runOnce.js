/**
 * scheduler/runOnce.js
 * Runs the full pipeline once immediately (no cron schedule).
 * Usage: npm run run-once
 */
import 'dotenv/config';
import { runAgent1 } from '../agents/agent1_eventFinder.js';
import { runAgent2 } from '../agents/agent2_companyResearcher.js';

const runStart = new Date();
console.log('\n' + '═'.repeat(60));
console.log(`🚀 Event Intelligence Pipeline started at ${runStart.toISOString()}`);
console.log('═'.repeat(60));

try {
  const agent1Result = await runAgent1();
  const agent2Result = await runAgent2();

  const duration = ((Date.now() - runStart.getTime()) / 1000).toFixed(1);
  console.log('\n' + '═'.repeat(60));
  console.log(`✅ Pipeline complete in ${duration}s`);
  console.log(`   Events: ${agent1Result.inserted} new, ${agent1Result.updated} updated`);
  console.log(`   Companies: ${agent2Result.companies} added`);
  console.log('═'.repeat(60) + '\n');
} catch (err) {
  console.error('\n❌ Pipeline error:', err);
  process.exit(1);
}
