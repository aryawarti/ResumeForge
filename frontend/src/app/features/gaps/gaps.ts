import { Component, inject, signal } from '@angular/core';

import { ForgeService } from '../../core/forge.service';
import { GapRoadmap } from '../../core/models';

/**
 * Gaps aggregated across every application.
 *
 * One rejection tells you nothing. Fifteen applications in, the same three
 * skills keep appearing in postings you cannot evidence, and that is a study
 * list drawn from the market you are actually applying into rather than from
 * someone's opinion about what engineers ought to know.
 */
@Component({
  selector: 'forge-gaps',
  standalone: true,
  template: `
    <header class="head">
      <p class="eyebrow">Roadmap</p>
      <h1>What the market keeps asking for</h1>
      @if (roadmap(); as r) {
        <p class="head__note">
          Across {{ r.total_applications }} application{{
            r.total_applications === 1 ? '' : 's'
          }}. Ranked by how often a posting wanted it and your resume could not
          evidence it.
        </p>
      }
    </header>

    @if (roadmap(); as r) {
      @if (r.items.length) {
        <ol class="ranks">
          @for (item of r.items; track item.skill) {
            <li class="rank sheet">
              <div
                class="rank__bar"
                [style.width.%]="(item.occurrences / r.items[0].occurrences) * 100"
                aria-hidden="true"
              ></div>
              <span class="rank__count mono">{{ item.occurrences }}</span>
              <div class="rank__body">
                <p class="rank__skill">{{ item.skill }}</p>
                <p class="rank__where">
                  {{ item.required_count }} required ·
                  {{ item.preferred_count }} preferred ·
                  {{ item.companies.join(', ') }}
                </p>
              </div>
            </li>
          }
        </ol>
      } @else {
        <div class="empty sheet">
          <h3>No gaps recorded yet</h3>
          <p>
            Tailor a few resumes and the pattern in what you are missing shows
            up here.
          </p>
        </div>
      }
    }
  `,
  styles: [
    `
      .head {
        padding: 2.5rem 0 1.75rem;
      }

      h1 {
        margin: 0.25rem 0 0.375rem;
        font-family: var(--mono);
        font-size: 1.75rem;
        letter-spacing: -0.03em;
      }

      .head__note {
        margin: 0;
        max-width: 56ch;
        font-size: 0.875rem;
        color: var(--graphite);
      }

      .ranks {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 0.5rem;
      }

      .rank {
        position: relative;
        display: grid;
        grid-template-columns: 3.5rem 1fr;
        align-items: center;
        gap: 1rem;
        padding: 0.875rem 1.125rem;
        overflow: hidden;
      }

      /* A quiet magnitude cue behind the row, not a chart. */
      .rank__bar {
        position: absolute;
        inset: 0 auto 0 0;
        background: var(--stet-wash);
        pointer-events: none;
      }

      .rank__count,
      .rank__body {
        position: relative;
      }

      .rank__count {
        font-size: 1.5rem;
        font-weight: 600;
        letter-spacing: -0.03em;
        text-align: right;
        color: var(--stet);
      }

      .rank__skill {
        margin: 0;
        font-family: var(--serif);
        font-size: 1rem;
      }

      .rank__where {
        margin: 0.2rem 0 0;
        font-family: var(--mono);
        font-size: 0.75rem;
        color: var(--faint);
      }
    `,
  ],
})
export class Gaps {
  private forge = inject(ForgeService);
  roadmap = signal<GapRoadmap | null>(null);

  constructor() {
    this.forge.gapRoadmap().subscribe((r) => this.roadmap.set(r));
  }
}
