/** The shell: what a first-time viewer is told, and what the controls say. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Layout } from './Layout';

function shell(overrides: Partial<Parameters<typeof Layout>[0]> = {}) {
  return (
    <Layout
      connection="ok"
      keyConfigured={false}
      onManageKey={() => {}}
      {...overrides}
    >
      <p>screen</p>
    </Layout>
  );
}

describe('identity', () => {
  it('says what the product is, not just what it is called', () => {
    // The single most important line for somebody opening this cold.
    render(shell());
    expect(screen.getByText('NexaAssist')).toBeInTheDocument();
    expect(screen.getByText('AI Support Platform')).toBeInTheDocument();
  });

  it('renders the screen it was given', () => {
    render(shell());
    expect(screen.getByText('screen')).toBeInTheDocument();
  });

  it('exposes one main landmark to skip to', () => {
    render(shell());
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('keeps the mark decorative, since the name is beside it', () => {
    // A logo that announces itself makes a screen reader say the product
    // name twice.
    const { container } = render(shell());
    const mark = container.querySelector('.brand__mark svg');
    expect(mark).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('the status group', () => {
  it('keeps both indicators together and apart from the control', () => {
    // They answer one question between them -- is the product working --
    // and the button beside them does something instead.
    const { container } = render(shell({ realtime: 'open' }));
    const group = container.querySelector('.shell__statuses');
    expect(group?.children).toHaveLength(2);
    expect(group?.querySelector('button')).toBeNull();
  });
});

describe('status', () => {
  it('reports backend readiness by name, not by colour', () => {
    // Colour is unavailable to a screen reader and to some readers entirely.
    render(shell({ connection: 'degraded' }));
    expect(screen.getByLabelText('Backend Degraded')).toBeInTheDocument();
  });

  it('reports realtime separately from readiness', () => {
    // Two different questions: an unavailable socket does not stop the
    // assistant answering, it only stops the answer arriving in pieces.
    render(shell({ connection: 'ok', realtime: 'reconnecting' }));
    expect(screen.getByLabelText('Backend Operational')).toBeInTheDocument();
    expect(screen.getByLabelText('Realtime Reconnecting…')).toBeInTheDocument();
  });

  it('omits the realtime indicator when there is no socket', () => {
    render(shell());
    expect(screen.queryByLabelText(/^Realtime/)).not.toBeInTheDocument();
  });

  it('announces a realtime change without the user watching for it', () => {
    render(shell({ realtime: 'open' }));
    expect(screen.getByLabelText('Realtime Live')).toHaveAttribute('aria-live', 'polite');
  });
});

describe('api access control', () => {
  it('says the key is not set yet', () => {
    render(shell({ keyConfigured: false }));
    expect(screen.getByRole('button', { name: 'Configure API access' })).toBeInTheDocument();
  });

  it('says the key is set, because a glyph alone cannot', () => {
    render(shell({ keyConfigured: true }));
    expect(
      screen.getByRole('button', { name: 'API access configured — manage it' }),
    ).toBeInTheDocument();
  });

  it('opens the panel when asked', async () => {
    const onManageKey = vi.fn();
    render(shell({ onManageKey }));
    await userEvent.click(screen.getByRole('button', { name: 'Configure API access' }));
    expect(onManageKey).toHaveBeenCalledOnce();
  });
});

describe('the sidebar drawer', () => {
  it('has no menu control when there is no sidebar to open', () => {
    render(shell());
    expect(screen.queryByRole('button', { name: /conversations/i })).not.toBeInTheDocument();
  });

  it('reports whether the drawer is open', () => {
    const onToggleSidebar = vi.fn();
    render(shell({ onToggleSidebar, sidebarOpen: false }));
    const toggle = screen.getByRole('button', { name: 'Show conversations' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('offers a way out of an open drawer that is not the menu button', async () => {
    // On a narrow screen the drawer covers the content; tapping beside it is
    // how people expect to dismiss one.
    const onToggleSidebar = vi.fn();
    render(shell({ onToggleSidebar, sidebarOpen: true }));
    await userEvent.click(screen.getByRole('button', { name: 'Close conversations' }));
    expect(onToggleSidebar).toHaveBeenCalledOnce();
  });

  it('has no scrim when the drawer is closed', () => {
    render(shell({ onToggleSidebar: () => {}, sidebarOpen: false }));
    expect(screen.queryByRole('button', { name: 'Close conversations' })).not.toBeInTheDocument();
  });
});
