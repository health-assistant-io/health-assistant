import { describe, it, expect, beforeEach } from 'vitest';
import {
  registerInstanceView,
  getInstanceView,
  _clearViewsForTests,
} from '../viewRegistry';

const DummyView: any = () => null;

describe('viewRegistry', () => {
  beforeEach(() => _clearViewsForTests());

  it('returns null for an unregistered type', () => {
    expect(getInstanceView('examination')).toBeNull();
  });

  it('registers and resolves a view by type', () => {
    registerInstanceView('examination', DummyView);
    expect(getInstanceView('examination')).toBe(DummyView);
  });

  it('re-registering replaces the view', () => {
    registerInstanceView('examination', DummyView);
    const next: any = () => null;
    registerInstanceView('examination', next);
    expect(getInstanceView('examination')).toBe(next);
  });
});
