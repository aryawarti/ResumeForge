import { Component, OnDestroy, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { ForgeService } from '../../core/forge.service';
import { ProgressService } from '../../core/progress.service';
import { Resume } from '../../core/models';

/**
 * Submit a posting and watch the run.
 *
 * The stage list is shown in full from the start, with completed stages
 * marked as they land. A sixty-second wait is easier to sit through when you
 * can see what the machine is doing and what is left, rather than a bar that
 * moves at an unexplained rate.
 */
@Component({
  selector: 'forge-tailor',
  standalone: true,
  imports: [FormsModule],
  template: `
    <header class="head">
      <p class="eyebrow">Tailor</p>
      <h1>Point this resume at one posting</h1>
    </header>

    <div class="split">
      <section class="sheet form">
        <label class="field">
          <span>Base resume</span>
          <select [(ngModel)]="resumeId">
            @for (r of resumes(); track r.id) {
              <option [value]="r.id">{{ r.name }}</option>
            }
          </select>
        </label>

        <div class="pair">
          <label class="field">
            <span>Company</span>
            <input type="text" [(ngModel)]="company" placeholder="Northwind" />
          </label>
          <label class="field">
            <span>Role</span>
            <input
              type="text"
              [(ngModel)]="role"
              placeholder="Senior Backend Engineer"
            />
          </label>
        </div>

        <label class="field">
          <span>Job description</span>
          <textarea
            rows="14"
            [(ngModel)]="jobText"
            placeholder="Paste the posting. Requirements, responsibilities, the lot — the more of its own wording, the better the vocabulary match."
          ></textarea>
        </label>

        <label class="field">
          <span>…or a link to it</span>
          <input
            type="text"
            [(ngModel)]="jobUrl"
            placeholder="https://…"
          />
        </label>

        @if (error()) {
          <p class="notice notice--error">{{ error() }}</p>
        }

        <button
          class="btn form__submit"
          (click)="submit()"
          [disabled]="running() || !resumeId"
        >
          {{ running() ? 'Running…' : 'Tailor this resume' }}
        </button>
      </section>

      <section class="sheet run">
        <p class="eyebrow">Progress</p>
        @if (!running() && !stages().length) {
          <p class="run__idle">
            Submit a posting and the run appears here, stage by stage.
          </p>
        } @else {
          <div
            class="bar"
            role="progressbar"
            [attr.aria-valuenow]="percent()"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <div class="bar__fill" [style.width.%]="percent()"></div>
          </div>
          <ol class="stages">
            @for (stage of stages(); track $index) {
              <li [class.stages__done]="stage.done">
                <span class="stages__mark" aria-hidden="true">
                  {{ stage.done ? '✓' : '·' }}
                </span>
                <span>{{ stage.message }}</span>
              </li>
            }
          </ol>
        }
      </section>
    </div>
  `,
  styles: [
    `
      .head {
        padding: 2.5rem 0 1.75rem;
      }

      h1 {
        margin: 0.25rem 0 0;
        font-family: var(--mono);
        font-size: 1.75rem;
        letter-spacing: -0.03em;
      }

      .split {
        display: grid;
        grid-template-columns: 1.4fr 1fr;
        gap: 1.25rem;
        align-items: start;
      }

      .form,
      .run {
        padding: 1.5rem;
      }

      .pair {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1rem;
      }

      .form__submit {
        width: 100%;
        justify-content: center;
      }

      .run {
        position: sticky;
        top: 1.25rem;
      }

      .run__idle {
        margin: 0.75rem 0 0;
        font-size: 0.875rem;
        color: var(--faint);
      }

      .bar {
        height: 2px;
        background: var(--rule);
        margin: 1rem 0 1.25rem;
        overflow: hidden;
      }

      .bar__fill {
        height: 100%;
        background: var(--insert);
        transition: width 0.4s ease;
      }

      .stages {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 0.5rem;
      }

      .stages li {
        display: grid;
        grid-template-columns: 1.25rem 1fr;
        gap: 0.25rem;
        font-size: 0.8125rem;
        color: var(--faint);
      }

      .stages__done {
        color: var(--ink);
      }

      .stages__mark {
        font-family: var(--mono);
        color: var(--keep);
      }

      @media (max-width: 900px) {
        .split {
          grid-template-columns: 1fr;
        }
        .run {
          position: static;
        }
      }
    `,
  ],
})
export class Tailor implements OnDestroy {
  private forge = inject(ForgeService);
  private progress = inject(ProgressService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private watching?: Subscription;

  resumes = signal<Resume[]>([]);
  stages = signal<{ message: string; done: boolean }[]>([]);
  percent = signal(0);
  running = signal(false);
  error = signal('');

  resumeId = '';
  company = '';
  role = '';
  jobText = '';
  jobUrl = '';

  constructor() {
    this.forge.listResumes().subscribe((list) => {
      this.resumes.set(list);
      const preset = this.route.snapshot.paramMap.get('resumeId');
      this.resumeId = preset ?? list[0]?.id ?? '';
    });
  }

  submit() {
    this.error.set('');
    this.running.set(true);
    this.stages.set([]);
    this.percent.set(0);

    this.forge
      .createGeneration({
        base_resume_id: this.resumeId,
        job_text: this.jobText,
        job_url: this.jobUrl,
        company: this.company,
        role: this.role,
      })
      .subscribe({
        next: (generation) => this.watch(generation.id),
        error: (err) => {
          this.running.set(false);
          this.error.set(err?.error?.detail ?? 'Could not start the run.');
        },
      });
  }

  private watch(id: string) {
    this.watching = this.progress.watch(id).subscribe({
      next: (update) => {
        if (update.event) {
          this.percent.set(update.event.percent);
          this.stages.update((list) => {
            const marked = list.map((s) => ({ ...s, done: true }));
            return [...marked, { message: update.event!.message, done: false }];
          });
        }
        if (update.done) {
          this.running.set(false);
          this.stages.update((list) => list.map((s) => ({ ...s, done: true })));
          if (update.status === 'completed') {
            this.router.navigate(['/generations', id]);
          } else {
            this.error.set(update.error || 'The run failed.');
          }
        }
      },
      error: () => {
        this.running.set(false);
        this.error.set(
          'Lost the progress stream. The run may still be going — check your generations.',
        );
      },
    });
  }

  ngOnDestroy() {
    this.watching?.unsubscribe();
  }
}
