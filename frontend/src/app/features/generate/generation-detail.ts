import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';

import { DiffView } from '../diff/diff-view';
import { ForgeService } from '../../core/forge.service';
import { CoverageItem, GenerationDetail as Detail } from '../../core/models';

/**
 * One tailored version: the diff, the coverage matrix, the gaps, the files.
 *
 * Coverage is shown as four distinct states rather than a score alone, because
 * they call for different responses. "Buried" is something the tool fixed;
 * "absent" is something only you can fix, and saying so plainly is more useful
 * than a percentage that hides it.
 */
@Component({
  selector: 'forge-generation-detail',
  standalone: true,
  imports: [DiffView],
  template: `
    @if (detail(); as d) {
      <header class="head">
        <div>
          <p class="eyebrow">
            {{ d.company || 'Untitled' }} · version {{ d.version_no }}
          </p>
          <h1>{{ d.role || 'Tailored resume' }}</h1>
          <p class="head__meta mono">
            from {{ d.base_resume_name }} · {{ d.page_count }} page{{
              d.page_count === 1 ? '' : 's'
            }}
            · {{ (d.coverage_score * 100).toFixed(0) }}% of requirements covered
          </p>
        </div>
        <div class="head__actions">
          @if (d.pdf_url) {
            <a class="btn" [href]="d.pdf_url" target="_blank" rel="noopener">
              Open PDF
            </a>
          }
          <button class="btn btn--ghost" (click)="downloadTex(d)">
            Download .tex
          </button>
        </div>
      </header>

      @if (d.status === 'failed') {
        <p class="notice notice--error">{{ d.error }}</p>
      }

      <nav class="tabs" role="tablist">
        @for (t of tabs; track t.id) {
          <button
            role="tab"
            [attr.aria-selected]="tab() === t.id"
            [class.tabs__on]="tab() === t.id"
            (click)="tab.set(t.id)"
          >
            {{ t.label }}
          </button>
        }
      </nav>

      @if (tab() === 'changes') {
        <forge-diff-view [report]="d.change_report" />
      }

      @if (tab() === 'coverage') {
        <p class="band__note">
          How each requirement in the posting maps onto your resume.
        </p>
        <ul class="coverage">
          @for (item of coverageItems(); track item.requirement) {
            <li class="sheet cov" [attr.data-status]="item.status">
              <span class="cov__tag">{{ statusLabel(item.status) }}</span>
              <div>
                <p class="cov__req">{{ item.requirement }}</p>
                <p class="cov__note">{{ item.note }}</p>
                @if (item.resume_phrasing) {
                  <p class="cov__phrase">
                    Your wording: <i>{{ item.resume_phrasing }}</i>
                  </p>
                }
              </div>
            </li>
          }
        </ul>
      }

      @if (tab() === 'gaps') {
        <p class="band__note">{{ d.gap_report.assessment }}</p>
        @if (d.gap_report.missing_required.length) {
          <h3 class="band">Required, not evidenced</h3>
          <ul class="gaps">
            @for (gap of d.gap_report.missing_required; track gap) {
              <li class="sheet gaps__item gaps__item--required">{{ gap }}</li>
            }
          </ul>
        }
        @if (d.gap_report.missing_preferred.length) {
          <h3 class="band">Preferred, not evidenced</h3>
          <ul class="gaps">
            @for (gap of d.gap_report.missing_preferred; track gap) {
              <li class="sheet gaps__item">{{ gap }}</li>
            }
          </ul>
        }
        @if (
          !d.gap_report.missing_required.length &&
          !d.gap_report.missing_preferred.length
        ) {
          <div class="empty sheet">
            <h3>Nothing missing</h3>
            <p>Your resume evidences every requirement this posting listed.</p>
          </div>
        }
      }

      @if (tab() === 'source') {
        <pre class="source sheet">{{ d.tex_source }}</pre>
      }
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

      .head__meta {
        margin: 0;
        color: var(--graphite);
        font-size: 0.8125rem;
      }

      .head__actions {
        display: flex;
        gap: 0.5rem;
        flex-shrink: 0;
      }

      .tabs {
        display: flex;
        gap: 1.5rem;
        border-bottom: 1px solid var(--rule);
        margin-bottom: 1.75rem;
      }

      .tabs button {
        padding: 0 0 0.625rem;
        border: 0;
        background: none;
        cursor: pointer;
        font-family: var(--mono);
        font-size: 0.75rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--faint);
        border-bottom: 2px solid transparent;
        margin-bottom: -1px;
      }

      .tabs__on {
        color: var(--ink);
        border-bottom-color: var(--ink);
      }

      .band {
        font-family: var(--mono);
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin: 1.75rem 0 0.75rem;
      }

      .band__note {
        max-width: 60ch;
        font-size: 0.875rem;
        color: var(--graphite);
      }

      .coverage,
      .gaps {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 0.5rem;
      }

      .cov {
        display: grid;
        grid-template-columns: 8.5rem 1fr;
        gap: 1rem;
        padding: 0.875rem 1.125rem;
        border-left-width: 2px;
      }

      .cov__tag {
        font-family: var(--mono);
        font-size: 0.6875rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        padding-top: 0.15rem;
      }

      .cov[data-status='covered_prominent'] {
        border-left-color: var(--keep);
      }
      .cov[data-status='covered_prominent'] .cov__tag {
        color: var(--keep);
      }

      .cov[data-status='covered_buried'],
      .cov[data-status='covered_rephrased'] {
        border-left-color: var(--insert);
      }
      .cov[data-status='covered_buried'] .cov__tag,
      .cov[data-status='covered_rephrased'] .cov__tag {
        color: var(--insert);
      }

      .cov[data-status='absent'] {
        border-left-color: var(--stet);
      }
      .cov[data-status='absent'] .cov__tag {
        color: var(--stet);
      }

      .cov__req {
        margin: 0;
        font-family: var(--serif);
        font-size: 0.9375rem;
      }

      .cov__note,
      .cov__phrase {
        margin: 0.25rem 0 0;
        font-size: 0.8125rem;
        color: var(--graphite);
      }

      .gaps__item {
        padding: 0.75rem 1.125rem;
        font-family: var(--serif);
        font-size: 0.9375rem;
        border-left: 2px solid var(--rule-strong);
      }

      .gaps__item--required {
        border-left-color: var(--stet);
      }

      .source {
        margin: 0;
        padding: 1.25rem;
        overflow-x: auto;
        font-family: var(--mono);
        font-size: 0.75rem;
        line-height: 1.6;
        max-height: 70vh;
      }

      @media (max-width: 720px) {
        .head {
          flex-direction: column;
        }
        .cov {
          grid-template-columns: 1fr;
          gap: 0.375rem;
        }
      }
    `,
  ],
})
export class GenerationDetailPage {
  private forge = inject(ForgeService);
  private route = inject(ActivatedRoute);

  readonly tabs = [
    { id: 'changes', label: 'Changes' },
    { id: 'coverage', label: 'Coverage' },
    { id: 'gaps', label: 'Gaps' },
    { id: 'source', label: 'Source' },
  ] as const;

  detail = signal<Detail | null>(null);
  tab = signal<string>('changes');
  error = signal('');

  readonly coverageItems = computed<CoverageItem[]>(
    () => this.detail()?.coverage?.items ?? [],
  );

  constructor() {
    const id = this.route.snapshot.paramMap.get('id')!;
    this.forge.getGeneration(id).subscribe({
      next: (d) => this.detail.set(d),
      error: (err) =>
        this.error.set(err?.error?.detail ?? 'Could not load that version.'),
    });
  }

  statusLabel(status: string): string {
    const labels: Record<string, string> = {
      covered_prominent: 'Prominent',
      covered_buried: 'Was buried',
      covered_rephrased: 'Reworded',
      absent: 'Not present',
    };
    return labels[status] ?? status;
  }

  downloadTex(d: Detail) {
    const blob = new Blob([d.tex_source], { type: 'application/x-tex' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${d.company || 'resume'}-v${d.version_no}.tex`;
    link.click();
    URL.revokeObjectURL(url);
  }
}
