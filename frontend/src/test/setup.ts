/** Shared test setup: DOM matchers, and cleanup between tests. */

import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Without this a component from one test is still mounted during the next,
// and a query that should find one element finds two.
afterEach(cleanup);
