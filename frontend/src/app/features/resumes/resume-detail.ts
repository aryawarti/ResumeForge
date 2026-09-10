import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';

import { ForgeService } from '../../core/forge.service';
import { ResumeDetail } from '../../core/models';

/**
 * What the parser saw.
 *
 * This screen exists because a silent misparse is the worst failure this
 * product has. If the parser mistook your project headings for job entries,
 * every downstream decision is built on that mistake -- and you would only
 * find out by reading the tailored PDF closely. So the structure is shown back
 * to you in the parser's own terms, before you ever generate against it:
 * these sections, these roles, these bullets. Counts that look wrong here are
 * a template-profile problem, and far better caught now than after five
 * applications.
 */
@Component({
  selector: 'forge-resume-detail',
  standalone: true,
  imports: [RouterLink, DatePipe],
  template: `
    @if (resume(); as r) {
      <header class="head">
        <div>
          <p class="eyebrow">Base resume</p>
          <h1>{{ r.name }}</h1>
          <p class="head__note mono">
            {{ r.template_profile }} · {{ r.page_count }} page{{
              r.page_count === 1 ? '' : 's'
            }}
            · added {{ r.created_at | date: 'd MMM y' }}
          </p>
        </div>
        <div class="head__actions">
          <a class="btn" [routerLink]="['/tailor', r.id]">Tailor this</a>
          <button class="btn btn--danger" (click)="remove()">Delete</button>
        </div>
      </header>

      @if (!r.compiles) {
        <p class="notice notice--error">
          This resume did not compile on upload. Tailoring it will almost
          certainly fail too &mdash; fix the source and re-upload.
        </p>
      }

      @for (warning of r.warnings; track warning) {
        <p class="notice notice--warn">{{ warning }}</p>
      }

      <section class="tally">
        <div class="tally__cell">
          <span class="tally__n mono">{{ r.stats.sections ?? 0 }}</span>
          <span class="tally__label">sections</span>
        </div>
        <div class="tally__cell">
          <span class="tally__n mono">{{ r.stats.entries ?? 0 }}</span>
          <span class="tally__label">entries</span>
        </div>
        <div class="tally__cell">
          <span class="tally__n mono">{{ r.stats.bullets ?? 0 }}</span>
          <span class="tally__label">bullets</span>
        </div>
      </section>

      <h2 class="sub">Structure the parser found</h2>

      @if (r.sections.length) {
        <ol class="sections">
          @for (section of r.sections; track section.id) {
            <li class="section sheet">
              <div>
                <p class="section__title">
                  {{ section.title }}
                  @if (section.is_skills) {
                    <span class="tag">skills</span>
                  }
                </p>
                <p class="section__counts mono">
                  @if (section.is_skills) {
                    regrouped as a whole, never rewritten
                  } @else {
                    {{ section.entries }} entr{{
                      section.entries === 1 ? 'y' : 'ies'
                    }}
                    · {{ section.bullets }} bullet{{
                      section.bullets === 1 ? '' : 's'
                    }}
                  }
                </p>
              </div>
              <span class="id">{{ section.id }}</span>
            </li>
          }
        </ol>
      } @else {
        <div class="empty sheet">
          <h3>No sections recognised</h3>
          <p>
            The template profile did not match anything editable, so tailoring
            would hand this file back unchanged.
          </p>
        </div>
      }

      <details class="source">
        <summary>LaTeX source ({{ lineCount() }} lines)</summary>
        <pre class="source__body">{{ r.tex_source }}</pre>
      </details>
    } @else if (error()) {
      <p class="notice notice--error">{{ error() }}</p>
    }
  `,
  styles: [
    `
      .head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 2rem;
        padding: 2.5rem 0 1.5rem;
      }

      h1 {
        margin: 0.25rem 0 0.375rem;
        font-family: var(--mono);
        font-size: 1.75rem;
        letter-spacing: -0.03em;
      }

      .head__note {
        margin: 0;
        color: var(--graphite);
        font-size: 0.8125rem;
      }

      .head__actions {
        display: flex;
        gap: 0.5rem;
        flex-shrink: 0;
      }

      .notice {
        margin-bottom: 0.75rem;
      }

      /* Three numbers, set large. If one of them is zero the parse is wrong,
       * and that should be legible from across the room. */
      .tally {
        display: flex;
        gap: 3rem;
        margin: 1.5rem 0 2.5rem;
        padding: 1.25rem 0.5rem;
        border-top: 1px solid var(--rule);
        border-bottom: 1px solid var(--rule);
      }

      .tally__cell {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
      }

      .tally__n {
        font-size: 1.75rem;
        font-weight: 600;
        letter-spacing: -0.04em;
      }

      .tally__label {
        font-size: 0.8125rem;
        color: var(--graphite);
      }

      .sub {
        margin: 0 0 0.875rem;
        font-size: 0.9375rem;
      }

      .sections {
        list-style: none;
        margin: 0 0 2.5rem;
        padding: 0;
        display: grid;
        gap: 0.375rem;
      }

      .section {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.75rem 1.125rem;
      }

      .section__title {
        margin: 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-family: var(--serif);
        font-size: 1rem;
      }

      .tag {
        font-family: var(--mono);
        font-size: 0.625rem;
        font-weight: 500;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--keep);
        background: var(--keep-wash);
        padding: 0.125rem 0.375rem;
        border-radius: var(--radius);
      }

      .section__counts {
        margin: 0.2rem 0 0;
        font-size: 0.75rem;
        color: var(--faint);
      }

      .source {
        border-top: 1px solid var(--rule);
        padding-top: 1rem;
      }

      .source summary {
        cursor: pointer;
        font-size: 0.875rem;
        color: var(--graphite);
      }

      .source__body {
        margin: 1rem 0 0;
        padding: 1.25rem;
        max-height: 30rem;
        overflow: auto;
        background: var(--sheet);
        border: 1px solid var(--rule);
        border-radius: var(--radius);
        font-family: var(--mono);
        font-size: 0.75rem;
        line-height: 1.65;
        white-space: pre;
      }

      @media (max-width: 640px) {
        .head {
          flex-direction: column;
          gap: 1rem;
        }

        .tally {
          gap: 1.75rem;
        }
      }
    `,
  ],
})
export class ResumeDetailPage {
  private forge = inject(ForgeService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  resume = signal<ResumeDetail | null>(null);
  error = signal('');

  readonly lineCount = computed(
    () => this.resume()?.tex_source.split('\n').length ?? 0,
  );

  constructor() {
    const id = this.route.snapshot.paramMap.get('id')!;
    this.forge.getResume(id).subscribe({
      next: (r) => this.resume.set(r),
      error: () => this.error.set('That resume could not be loaded.'),
    });
  }

  remove() {
    const r = this.resume();
    if (!r) return;
    // Generated versions are independent and deliberately survive this.
    const ok = confirm(
      `Delete "${r.name}"? Versions already tailored from it are kept.`,
    );
    if (!ok) return;

    this.forge.deleteResume(r.id).subscribe({
      next: () => this.router.navigate(['/resumes']),
      error: () => this.error.set('Could not delete that resume.'),
    });
  }
}
