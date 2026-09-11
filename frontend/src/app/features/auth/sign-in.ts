import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { finalize } from 'rxjs';

import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'forge-sign-in',
  standalone: true,
  imports: [FormsModule],
  template: `
    <main class="gate">
      <div class="gate__pitch">
        <p class="eyebrow">ResumeForge</p>
        <h1>
          Tailor a LaTeX resume to one posting<br />
          without inventing anything.
        </h1>
        <p class="lede">
          Upload your <code>.tex</code> once. Paste a job description. Get back
          a compiled resume with every change explained &mdash; and every
          invented skill blocked before it reaches the page.
        </p>

        <dl class="claims">
          <div>
            <dt>Formatting survives</dt>
            <dd>
              Your file is parsed into a tree and edited by node. Re-rendering
              an untouched resume returns the same bytes it went in as.
            </dd>
          </div>
          <div>
            <dt>Nothing is invented</dt>
            <dd>
              A rewrite cannot add a technology, a figure, or a claim of
              seniority. Refused edits are shown to you, not hidden.
            </dd>
          </div>
          <div>
            <dt>Gaps are reported honestly</dt>
            <dd>
              What the posting wanted and you do not have gets listed, not
              papered over. Across applications it becomes a study list.
            </dd>
          </div>
        </dl>
      </div>

      <div class="gate__form sheet">
        <div class="tabs" role="tablist">
          <button
            role="tab"
            [attr.aria-selected]="mode() === 'in'"
            [class.tabs__on]="mode() === 'in'"
            (click)="mode.set('in')"
          >
            Sign in
          </button>
          <button
            role="tab"
            [attr.aria-selected]="mode() === 'up'"
            [class.tabs__on]="mode() === 'up'"
            (click)="mode.set('up')"
          >
            Create account
          </button>
        </div>

        <form (ngSubmit)="submit()">
          @if (mode() === 'up') {
            <label class="field">
              <span>Name</span>
              <input type="text" name="name" [(ngModel)]="name" autocomplete="name" />
            </label>
          }

          <label class="field">
            <span>Email</span>
            <input
              type="email"
              name="email"
              [(ngModel)]="email"
              autocomplete="email"
              required
            />
          </label>

          <label class="field">
            <span>Password</span>
            <input
              type="password"
              name="password"
              [(ngModel)]="password"
              [attr.autocomplete]="
                mode() === 'up' ? 'new-password' : 'current-password'
              "
              required
            />
            @if (mode() === 'up') {
              <small>At least 10 characters.</small>
            }
          </label>

          @if (error()) {
            <p class="notice notice--error">{{ error() }}</p>
          }

          @if (slow()) {
            <p class="notice">
              Still waiting on the server. On Render's free plan the API
              sleeps after 15 idle minutes and takes up to a minute to wake.
            </p>
          }

          <button class="btn gate__submit" type="submit" [disabled]="busy()">
            {{ busy() ? 'Working…' : mode() === 'up' ? 'Create account' : 'Sign in' }}
          </button>
        </form>
      </div>
    </main>
  `,
  styles: [
    `
      .gate {
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
        gap: 4rem;
        align-items: start;
        max-width: 1080px;
        margin: 0 auto;
        padding: 5rem 2.5rem;
      }

      h1 {
        margin: 0.75rem 0 1rem;
        font-family: var(--mono);
        font-size: clamp(1.75rem, 3.2vw, 2.5rem);
        font-weight: 600;
        line-height: 1.18;
        letter-spacing: -0.035em;
      }

      .lede {
        max-width: 46ch;
        font-size: 1rem;
        color: var(--graphite);
      }

      code {
        font-family: var(--mono);
        font-size: 0.9em;
        background: var(--sheet);
        border: 1px solid var(--rule);
        border-radius: 2px;
        padding: 0.05em 0.3em;
      }

      .claims {
        margin: 2.5rem 0 0;
        display: grid;
        gap: 1.25rem;
      }

      .claims div {
        padding-left: 1rem;
        border-left: 2px solid var(--rule-strong);
      }

      .claims dt {
        font-family: var(--mono);
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }

      .claims dd {
        margin: 0.3rem 0 0;
        font-size: 0.875rem;
        color: var(--graphite);
        max-width: 48ch;
      }

      .gate__form {
        padding: 1.75rem;
      }

      .tabs {
        display: flex;
        gap: 1.25rem;
        border-bottom: 1px solid var(--rule);
        margin-bottom: 1.5rem;
      }

      .tabs button {
        padding: 0 0 0.625rem;
        border: 0;
        background: none;
        cursor: pointer;
        font-size: 0.875rem;
        font-weight: 500;
        color: var(--faint);
        border-bottom: 2px solid transparent;
        margin-bottom: -1px;
      }

      .tabs__on {
        color: var(--ink);
        border-bottom-color: var(--ink);
      }

      small {
        display: block;
        margin-top: 0.3rem;
        font-size: 0.75rem;
        color: var(--faint);
      }

      .gate__submit {
        width: 100%;
        justify-content: center;
        margin-top: 0.5rem;
      }

      @media (max-width: 860px) {
        .gate {
          grid-template-columns: 1fr;
          gap: 2.5rem;
          padding: 2.5rem 1.5rem;
        }
      }
    `,
  ],
})
export class SignIn {
  private auth = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  mode = signal<'in' | 'up'>('in');
  busy = signal(false);
  error = signal('');
  /** Set when a request takes longer than a warm server ever would. */
  slow = signal(false);

  name = '';
  email = '';
  password = '';

  submit() {
    this.error.set('');
    this.busy.set(true);
    this.slow.set(false);
    const slowTimer = setTimeout(() => this.slow.set(true), 5000);

    const request =
      this.mode() === 'up'
        ? this.auth.register(this.email, this.password, this.name)
        : this.auth.login(this.email, this.password);

    const settled = () => {
      clearTimeout(slowTimer);
      this.slow.set(false);
    };

    request.pipe(finalize(settled)).subscribe({
      next: () => {
        this.busy.set(false);
        const next = this.route.snapshot.queryParamMap.get('next') ?? '/resumes';
        this.router.navigateByUrl(next);
      },
      error: (err) => {
        this.busy.set(false);
        // Say what went wrong and what to do, in the interface's voice.
        this.error.set(
          err?.error?.detail ??
            'Could not reach the server. Check that the API is running.',
        );
      },
    });
  }
}
