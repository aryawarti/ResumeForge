import { Routes } from '@angular/router';

import { authGuard, guestGuard } from './core/auth.guard';

/**
 * Every screen is lazily loaded. The diff view in particular pulls in a fair
 * amount of rendering code, and there is no reason to pay for it on the
 * sign-in page.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'resumes' },

  {
    path: 'sign-in',
    title: 'Sign in · ResumeForge',
    canActivate: [guestGuard],
    loadComponent: () => import('./features/auth/sign-in').then((m) => m.SignIn),
  },

  {
    path: 'resumes',
    title: 'Base resumes · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/resumes/resumes').then((m) => m.Resumes),
  },
  {
    path: 'resumes/:id',
    title: 'Resume · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/resumes/resume-detail').then((m) => m.ResumeDetailPage),
  },

  {
    path: 'tailor',
    title: 'Tailor · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/generate/tailor').then((m) => m.Tailor),
  },
  {
    // Same screen, arriving with a base resume already chosen.
    path: 'tailor/:resumeId',
    title: 'Tailor · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/generate/tailor').then((m) => m.Tailor),
  },

  {
    path: 'generations',
    title: 'Applications · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/generate/generations').then((m) => m.Generations),
  },
  {
    path: 'generations/:id',
    title: 'Application · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/generate/generation-detail').then(
        (m) => m.GenerationDetailPage,
      ),
  },

  {
    path: 'gaps',
    title: 'Gaps · ResumeForge',
    canActivate: [authGuard],
    loadComponent: () => import('./features/gaps/gaps').then((m) => m.Gaps),
  },

  { path: '**', redirectTo: 'resumes' },
];
