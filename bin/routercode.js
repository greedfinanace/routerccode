#!/usr/bin/env node

const { spawn } = require('child_process');
const path = require('path');

// Try to execute the 'routercode' command directly.
// After 'pip install .', it should be in the user's path.
const args = process.argv.slice(2);
const pythonProcess = spawn('routercode', args, {
  stdio: 'inherit',
  shell: true
});

pythonProcess.on('error', (err) => {
  // If 'routercode' isn't found, try running it as a module
  const fallbackProcess = spawn('python', ['-m', 'openrouter_agent.main', ...args], {
    stdio: 'inherit',
    shell: true
  });

  fallbackProcess.on('error', (fallbackErr) => {
    console.error('Error: Could not execute RouterCode. Ensure Python is installed and configured.');
    process.exit(1);
  });

  fallbackProcess.on('exit', (code) => {
    process.exit(code);
  });
});

pythonProcess.on('exit', (code) => {
  if (code !== null) {
    process.exit(code);
  }
});
