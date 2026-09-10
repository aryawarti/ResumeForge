import { Component, computed, input } from '@angular/core';

import { BulletDiff, ChangeReport, Reordering } from '../../core/models';

/**
 * The change report, rendered as a galley proof.
 *
 * Layout reasoning: a plain two-column before/after represents rewriting well
 * and reordering terribly -- moves become crossing lines between mirrored
 * lists. Since reordering is the highest-value operation here, each change is
 * one row instead: a move marker in the left gutter, the before/after pair
 * side by side, and the agent's stated reason in a margin rail on the right.
 *
 * Refused edits appear in the same rail rather than being hidden. A guard that
 * silently drops a third of the plan is not visibly trustworthy, and "we
 * declined to add Kubernetes" is the most reassuring thing this page can say.
 */
@Component({
  selector: 'forge-diff-view',
  standalone: true,
  template: `
    @if (report(); as r) {
      @if (r.strategy) {
        <section class="strategy sheet">
          <p class="eyebrow">Approach</p>
          <p class="strategy__text">{{ r.strategy }}</p>
        </section>
      }

      <div class="tally">
        <span class="tally__item">
          <b>{{ r.reorderings.length }}</b> moved
        </span>
        <span class="tally__item">
          <b>{{ applied().length }}</b> rewritten
        </span>
        <span class="tally__item tally__item--stet">
          <b>{{ refused().length }}</b> refused
        </span>
      </div>

      @if (r.reorderings.length) {
        <h3 class="band">Reordered</h3>
        <p class="band__note">
          Moves change what a reviewer reads first. They carry no factual risk,
          so they come before any rewriting.
        </p>
        @for (move of r.reorderings; track move.target_id + move.op) {
          <article class="row sheet">
            <div class="row__gutter row__gutter--move" aria-hidden="true">⇅</div>
            <div class="row__body">
              <p class="row__op mono">{{ label(move) }}</p>
              <p class="row__detail">{{ move.detail }}</p>
            </div>
            <aside class="rail">
              <p class="rail__reason">{{ move.reason }}</p>
            </aside>
          </article>
        }
      }

      @if (applied().length) {
        <h3 class="band">Rewritten</h3>
        <p class="band__note">
          Wording only. Every rewrite was checked for invented technologies,
          figures, and claims of seniority before it was applied.
        </p>
        @for (diff of applied(); track diff.bullet_id) {
          <article class="row row--diff sheet">
            <div class="row__gutter" aria-hidden="true">
              {{ diff.op === 'drop_bullet' ? '×' : '≠' }}
            </div>
            <div class="pane pane--before">
              <p class="eyebrow">Yours</p>
              <p class="bullet">
                @for (seg of diff.segments; track $index) {
                  @if (seg.type === 'equal') {
                    <span>{{ seg.text }} </span>
                  } @else if (seg.type === 'removed') {
                    <del>{{ seg.text }}</del>
                    <span> </span>
                  }
                }
              </p>
            </div>
            <div class="pane pane--after">
              <p class="eyebrow">Tailored</p>
              <p class="bullet">
                @for (seg of diff.segments; track $index) {
                  @if (seg.type === 'equal') {
                    <span>{{ seg.text }} </span>
                  } @else if (seg.type === 'added') {
                    <ins>{{ seg.text }}</ins>
                    <span> </span>
                  }
                }
              </p>
            </div>
            <aside class="rail">
              <p class="rail__reason">{{ diff.reason }}</p>
              <p class="id">{{ diff.bullet_id }}</p>
            </aside>
          </article>
        }
      }

      @if (refused().length) {
        <h3 class="band band--stet">Refused</h3>
        <p class="band__note">
          The agent proposed these and the guards rejected them. Nothing below
          reached your resume.
        </p>
        @for (diff of refused(); track diff.bullet_id + diff.rejection) {
          <article class="row row--stet sheet">
            <div class="row__gutter row__gutter--stet" aria-hidden="true">✕</div>
            <div class="row__body">
              <p class="eyebrow">Proposed</p>
              <p class="bullet bullet--struck">{{ diff.after }}</p>
            </div>
            <aside class="rail rail--stet">
              <p class="rail__verdict">{{ diff.rejection }}</p>
            </aside>
          </article>
        }
      }

      @if (!r.reorderings.length && !applied().length && !refused().length) {
        <div class="empty sheet">
          <h3>No changes were needed</h3>
          <p>
            This resume already covers the posting in the order a reviewer
            reads. Send it as it is.
          </p>
        </div>
      }
    }
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .strategy {
        padding: 1rem 1.25rem;
        margin-bottom: 1.5rem;
        border-left: 2px solid var(--ink);
      }

      .strategy__text {
        margin: 0.375rem 0 0;
        font-family: var(--serif);
        font-size: 1.0625rem;
        line-height: 1.5;
        max-width: var(--measure);
      }

      .tally {
        display: flex;
        gap: 1.75rem;
        padding-bottom: 1.25rem;
        border-bottom: 1px solid var(--rule);
        margin-bottom: 2rem;
      }

      .tally__item {
        font-family: var(--mono);
        font-size: 0.75rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--graphite);
      }

      .tally__item b {
        font-size: 1.375rem;
        font-weight: 600;
        color: var(--ink);
        margin-right: 0.3rem;
        letter-spacing: -0.02em;
      }

      .tally__item--stet b {
        color: var(--stet);
      }

      .band {
        font-family: var(--mono);
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin: 2.25rem 0 0.25rem;
      }

      .band--stet {
        color: var(--stet);
      }

      .band__note {
        max-width: 56ch;
        margin: 0 0 1rem;
        font-size: 0.8125rem;
        color: var(--graphite);
      }

      /* Gutter · content · reason rail. The rail is the signature: every
         change carries its justification physically beside it. */
      .row {
        display: grid;
        grid-template-columns: 2.25rem 1fr 15rem;
        margin-bottom: 0.625rem;
        overflow: hidden;
      }

      .row--diff {
        grid-template-columns: 2.25rem 1fr 1fr 15rem;
      }

      .row__gutter {
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--vellum);
        border-right: 1px solid var(--rule);
        font-family: var(--mono);
        font-size: 0.875rem;
        color: var(--faint);
      }

      .row__gutter--move {
        color: var(--insert);
      }

      .row__gutter--stet {
        color: var(--stet);
        background: var(--stet-wash);
      }

      .row__body,
      .pane {
        padding: 0.875rem 1.125rem;
      }

      .pane--after {
        border-left: 1px solid var(--rule);
        background: color-mix(in srgb, var(--insert-wash) 28%, transparent);
      }

      .row__op {
        margin: 0;
        font-size: 0.8125rem;
        font-weight: 500;
      }

      .row__detail {
        margin: 0.25rem 0 0;
        font-size: 0.8125rem;
        color: var(--graphite);
      }

      /* Serif marks this as the document's own content, not app chrome. */
      .bullet {
        margin: 0.375rem 0 0;
        font-family: var(--serif);
        font-size: 0.9375rem;
        line-height: 1.55;
      }

      .bullet--struck {
        color: var(--graphite);
        text-decoration: line-through;
        text-decoration-color: var(--stet);
        text-decoration-thickness: 1px;
      }

      del {
        background: var(--strike-wash);
        color: var(--strike);
        text-decoration-thickness: 1px;
        padding: 0 0.1em;
      }

      ins {
        background: var(--insert-wash);
        color: var(--insert);
        text-decoration: none;
        padding: 0 0.1em;
        font-weight: 500;
      }

      .rail {
        padding: 0.875rem 1.125rem;
        border-left: 1px solid var(--rule);
        background: var(--vellum);
      }

      .rail--stet {
        background: var(--stet-wash);
        border-left-color: color-mix(in srgb, var(--stet) 35%, transparent);
      }

      .rail__reason,
      .rail__verdict {
        margin: 0;
        font-size: 0.8125rem;
        line-height: 1.45;
        color: var(--graphite);
      }

      .rail__verdict {
        color: #6b5417;
        font-weight: 450;
      }

      .rail .id {
        margin: 0.5rem 0 0;
      }

      @media (max-width: 900px) {
        .row,
        .row--diff {
          grid-template-columns: 2.25rem 1fr;
        }

        .pane--after,
        .rail {
          grid-column: 2;
          border-left: 0;
          border-top: 1px solid var(--rule);
        }
      }
    `,
  ],
})
export class DiffView {
  readonly report = input.required<ChangeReport | undefined>();

  readonly applied = computed<BulletDiff[]>(
    () => this.report()?.diffs.filter((d) => d.applied) ?? [],
  );

  readonly refused = computed<BulletDiff[]>(
    () => this.report()?.diffs.filter((d) => !d.applied) ?? [],
  );

  label(move: Reordering): string {
    const names: Record<string, string> = {
      reorder_bullets: 'Bullets reordered',
      reorder_entries: 'Roles reordered',
      reorder_sections: 'Sections reordered',
    };
    return names[move.op] ?? move.op;
  }
}
