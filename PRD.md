# PRD — Ranch Cloud Credits (RCC) Hybrid Monetization pour ComfyUI SaaS (V1)
**Produit :** ComfyUI Manager (portail SaaS) + ComfyUI Docker GPU  
**Entreprise :** Ranch Computing  
**Auteur :** Product Management (Dominique COCO)  
**Date :** 2026-02-05  
**Statut :** Draft V1 (POC-ready)  
**Cible :** Déclencher un POC fonctionnel via GitHub Copilot + Agents IA

---

## 0) Résumé exécutif (V1)
Nous lançons une **V1 commerciale** de ComfyUI en SaaS (Docker + GPU) monétisée via un modèle **hybride** :

1) **Abonnement** (mensuel/annuel) = accès au service + **bundle RCC** crédité **chaque mois** (même si paiement annuel).  
2) **Top-up** (achat de packs RCC) = crédits consommables à la demande.  
3) **Consommation** RCC par tâche de compute :
   - **Image Task** → **1 RCC**
   - **Vidéo Task** → **5 RCC**
4) **Admin** : accès de test sans paiement via **GitLab OAuth**, strictement protégé, avec **audit complet**.

✅ Décision V1 : **solde RCC au niveau utilisateur**.  
📌 V2 : **solde RCC au niveau organisation** (indispensable), en complément du user-level.

---

## 1) Contexte & Problème
ComfyUI est puissant mais non prêt “out-of-the-box” pour une exploitation SaaS commerciale :
- aucune monétisation native,
- gestion modèles non gouvernée,
- sécurité & exposition réseau à maîtriser,
- besoin d’un portail unifié pour les opérations (start/stop), logs, facturation.

Ranch Computing possède déjà un mécanisme de crédits (“Ranch Crédits”) ; pour les solutions cloud, l’unité devient **Ranch Cloud Credits (RCC)**. Ce PRD décrit comment intégrer RCC dans une V1 simple, robuste et monétisable.

---

## 2) Objectifs (SMART)
1. **Monétiser** l’usage ComfyUI via RCC (abonnement + top-up) avec une règle de coût simple (1/5 RCC).
2. **Bloquer** les exécutions si RCC insuffisants (sauf admin).
3. **Tracer** toutes les actions (jobs, crédits, paiements) via un **ledger** auditable.
4. **Sécuriser** l’accès admin via GitLab OAuth (bypass paiement uniquement pour admins).
5. Livrer un **POC fonctionnel** en < 3 sprints, déployable localement / staging.

---

## 3) Non-objectifs (V1)
- Modulation dynamique du coût RCC selon résolution/steps/durée (→ V2).
- Multi-tenant strict par instance (une instance ComfyUI par client) (→ roadmap selon stratégie).
- Marketplace de modèles et gouvernance avancée (→ V2/V3).
- Orchestration K8s multi-nœuds (→ V2/V3).

---

## 4) Hypothèses & principes (V1)
- **ComfyUI** tourne en Docker avec GPU NVIDIA (Windows 11 + WSL2 dans le POC, extensible).
- Le portail est en **FastAPI** (UI + API).
- La base de données cible est **Supabase** ; **SQLite** est un fallback pour POC offline.
- **Stripe** est le PSP (Checkout + Webhooks), et les **webhooks** sont la source de vérité.
- Toutes les consommations RCC sont **auditées** (ledger).
- Exposition Internet : pas de ComfyUI public non-authentifié (tunnel sécurisé / reverse proxy).

---

## 5) Personas & parcours

### Persona A — Utilisateur payant
- veut exécuter des workflows et récupérer des outputs,
- achète un abonnement, et éventuellement des packs RCC.

**Parcours :**
1) S’inscrire / se connecter  
2) Voir solde RCC  
3) Lancer une tâche (image/vidéo)  
4) Consommer RCC, récupérer output, voir historique jobs

### Persona B — Admin (Ranch)
- veut tester la stack et opérer le service,
- sans passer par paiement,
- nécessite une identité “forte” (GitLab).

**Parcours :**
1) Se connecter via GitLab OAuth  
2) Accéder au dashboard admin  
3) Démarrer/stopper ComfyUI, installer des modèles  
4) Gérer utilisateurs, ajuster RCC, consulter logs & jobs

---

## 6) Glossaire
- **RCC** : Ranch Cloud Credits (unité de consommation).
- **Ledger RCC** : journal des mouvements RCC (crédit/débit), source de vérité.
- **Job / Compute Task** : exécution d’un workflow ComfyUI.
- **Reserve/Capture/Release** : mécanisme de réservation/consommation/remboursement.

---

## 7) Modèle économique (V1)

### 7.1 Unité de facturation
- 1 RCC = **1 tâche compute Image**  
- 5 RCC = **1 tâche compute Vidéo**

### 7.2 Abonnement (mensuel / annuel)
- Donne accès au service + crédit RCC “bundle” **mensuel**.
- Si paiement annuel : **remise** possible, mais crédit RCC **mensuel** (simplicité V1).

> **Notes pricing :** ce PRD ne fixe pas le prix en €. Il définit le mécanisme. Les montants seront arbitrés avec le coût infra + marge.

### 7.3 Top-up RCC
- Packs RCC (S/M/L) achetés à la demande.
- Crédités via webhook Stripe après paiement.

### 7.4 Priorité de consommation (V1)
- Solde RCC global unique (pas de séparation “inclus vs top-up”).

---

## 8) Règles de consommation RCC (V1)

### 8.1 Détermination du coût
- À la création du job, le type est fixé :
  - `IMAGE_TASK` → cost = 1
  - `VIDEO_TASK` → cost = 5

### 8.2 Politique Reserve/Release simplifiée (V1)
- **Reserve = débit immédiat** à `JOB_CREATED` (delta négatif).
- **Release = remboursement total** à `JOB_FAILED` (delta positif).
- **Succès** : pas d’opération supplémentaire (puisque déjà débité).

**Avantage :** simplicité, robuste, idempotent si bien conçu.

### 8.3 Admin bypass
- Admin peut exécuter des jobs sans impacter le solde, mais on log :
  - soit un ledger delta=0 avec reason `ADMIN_BYPASS`,
  - soit une entrée job “bypass=true”.

---

## 9) Périmètre fonctionnel (V1)

### 9.1 Auth & rôles
- Utilisateur standard : accès soumis à RCC
- Admin : GitLab OAuth, `is_admin=true`, bypass paiement

### 9.2 Wallet RCC
- Affichage solde RCC
- Blocage exécution si solde insuffisant
- Historique transactions RCC (ledger)

### 9.3 Jobs / Compute
- Création job (type, coût)
- Suivi statut (queued/running/succeeded/failed)
- Outputs accessibles (fichiers ou URL)
- Durée / timestamps
- Logs d’exécution

### 9.4 Paiement Stripe
- Checkout top-up packs
- Checkout abonnement (mensuel/annuel)
- Webhooks :
  - top-up : `checkout.session.completed`
  - subscription : `invoice.paid` (ou équivalent)
- Idempotence par `stripe_event_id`

### 9.5 Gestion ComfyUI (Ops)
- start/stop/restart/status
- gestion des modèles (admin only) :
  - install via URL
  - list
  - delete

### 9.6 Dashboard admin (V1)
- KPIs : users, jobs, RCC consommés, erreurs récentes
- Users : liste + ajustement RCC + rôle admin
- Jobs : liste + filtres
- Logs : export CSV (option V1 si quick-win)
- Modèles : liste/install/delete
- Ops : start/stop/status

---

## 10) Exigences fonctionnelles détaillées (FR)

### FR-01 — Affichage solde RCC
**Description :** le portail affiche le solde RCC actuel de l’utilisateur.  
**Critères d’acceptation :**
- Solde cohérent avec somme du ledger
- Visible sur la page principale (header)

### FR-02 — Création job Image/Vidéo + coût RCC
**Description :** l’utilisateur peut créer un job de type image ou vidéo.  
**AC :**
- `IMAGE_TASK` coûte 1 RCC
- `VIDEO_TASK` coûte 5 RCC
- coût stocké dans l’objet job

### FR-03 — Blocage si RCC insuffisants
**AC :**
- si solde < coût : réponse 402/403 + message “solde insuffisant”
- aucune exécution ComfyUI déclenchée

### FR-04 — Débit RCC à la création (reserve simplifiée)
**AC :**
- création job non-admin → écrit ledger `JOB_RESERVE` delta négatif
- job créé avec status `created/reserved`

### FR-05 — Remboursement total si échec
**AC :**
- job `failed` → ledger `JOB_RELEASE` delta positif (montant identique au coût)
- solde restitué

### FR-06 — Admin bypass via GitLab OAuth
**AC :**
- login GitLab fonctionne (authorize + callback)
- user admin accède au dashboard
- jobs admin ne débitent pas RCC mais sont loggés

### FR-07 — Top-up RCC via Stripe
**AC :**
- création checkout session pack
- webhook crédite ledger `TOPUP_GRANT` (delta positif)
- idempotence : un event Stripe ne crédite qu’une fois

### FR-08 — Abonnement via Stripe
**AC :**
- checkout subscription OK
- `invoice.paid` (ou event retenu) crédite ledger `SUBSCRIPTION_GRANT` mensuellement
- idempotence + audit payment

### FR-09 — Gestion modèles (admin)
**AC :**
- install URL → fichier présent dans `models/checkpoints`
- list models affiche nom + taille + date (au minimum)
- delete supprime le fichier (et log)

### FR-10 — Ops ComfyUI
**AC :**
- start/stop/status reflètent l’état container
- logs d’opération persistés

---

## 11) Exigences non-fonctionnelles (NFR)

### Sécurité
- NFR-S1 : ComfyUI n’est pas exposé sans auth.
- NFR-S2 : Secrets via variables d’environnement (.env en POC, secret manager ensuite).
- NFR-S3 : Sessions sécurisées (cookie httpOnly en prod).
- NFR-S4 : Rate limiting sur login, webhooks, install modèles.

### Fiabilité & audit
- NFR-R1 : Ledger RCC est source de vérité (audit).
- NFR-R2 : Webhooks Stripe idempotents.
- NFR-R3 : Logs structurés (JSON) + rotation.

### Performance
- NFR-P1 : Page dashboard < 2s sur datasets modestes (V1).
- NFR-P2 : Création job < 500ms (hors exécution compute).

---

## 12) Architecture (V1)

### Composants
- **FastAPI Portal** : UI + API + orchestration jobs + billing hooks
- **DB (Supabase)** : users, jobs, rcc_ledger, payments, logs
- **Stripe** : checkout + webhooks
- **GitLab OAuth** : admin authentication
- **ComfyUI Docker** : exécution GPU + volumes (models/outputs/workflows)

### Flux principaux
1) User → Portal → vérif RCC → création job → ComfyUI → output + status  
2) Stripe → webhook → Portal → ledger RCC crédité  
3) Admin → GitLab OAuth → dashboard → opérations + gestion

---

## 13) Modèle de données (V1)

> **Note :** champs exacts à adapter selon Supabase Auth vs auth interne. Le minimum ci-dessous est stable.

### 13.1 `users`
- `id` (uuid ou bigint)
- `email`
- `is_admin` (bool)
- `created_at`

### 13.2 `jobs`
- `id`
- `user_id`
- `type` (`IMAGE_TASK` / `VIDEO_TASK`)
- `cost_rcc` (1 / 5)
- `status` (`created|running|succeeded|failed`)
- `duration_ms`
- `output_uri`
- `metadata` (json)
- `created_at`, `started_at`, `ended_at`

### 13.3 `rcc_ledger` (CRITIQUE)
- `id`
- `user_id`
- `delta` (int)
- `reason` (enum)
  - `JOB_RESERVE`
  - `JOB_RELEASE`
  - `SUBSCRIPTION_GRANT`
  - `TOPUP_GRANT`
  - `MANUAL_ADJUST`
  - `ADMIN_BYPASS`
- `job_id` (nullable)
- `external_ref` (nullable : stripe_event_id, invoice_id, etc.)
- `created_at`

### 13.4 `payments` (audit)
- `id`
- `user_id`
- `provider` = `stripe`
- `type` = `subscription|topup`
- `amount`, `currency`
- `status`
- `external_ref`
- `created_at`

### 13.5 `logs` (ops & audit)
- `id`
- `user_id` (nullable)
- `ip`
- `action`
- `status`
- `created_at`

---

## 14) API Contract (V1)

### Auth
- `GET /auth/gitlab` → redirect OAuth (admin)
- `GET /auth/gitlab/callback` → session admin
- `POST /auth/login` / `POST /auth/logout` (si nécessaire en POC)

### User
- `GET /me` → profil + solde RCC
- `GET /jobs` → liste jobs user
- `POST /jobs` → crée job + débite RCC (sauf admin)
- `GET /jobs/{id}` → détail job

### Payments
- `POST /checkout/topup` → crée session checkout pack
- `POST /checkout/subscription` → crée session subscription
- `POST /webhooks/stripe` → traitement events (idempotent)

### Admin
- `GET /admin/dashboard`
- `GET /admin/users`
- `PATCH /admin/users/{id}` (ajustement RCC, is_admin)
- `GET /admin/jobs`
- `POST /admin/models/install`
- `DELETE /admin/models/{name}`
- `POST /admin/comfyui/start|stop|restart`
- `GET /admin/comfyui/status`

---

## 15) Dashboard Admin (V1) — KPIs & vues

### KPIs minimum
- Total users
- Users actifs (solde > 0 ou plan actif)
- Jobs (24h/7j)
- RCC débités (jour/semaine)
- Erreurs jobs & erreurs paiement
- Modèles installés

### Vues
- Users : email, is_admin, solde RCC, actions (adjust)
- Jobs : type, cost, status, durée, output
- Modèles : liste + install URL + delete
- Ops : état ComfyUI + start/stop/restart

---

## 16) KPI Produit (V1)
- Conversion checkout → crédit RCC effectif (%)
- MAU / WAU (utilisateurs ayant exécuté ≥ 1 job)
- RCC consommés / jour
- Taux d’échec jobs (%)
- Temps moyen job (image/vidéo)

---

## 17) SLA / SLO (V1 — POC)
- Uptime portail (staging) : best-effort
- RPO/RTO : à définir (POC)
- Journalisation : conservation 7–30 jours (selon stockage)

---

## 18) Risques & mitigations
- **R1 : fraude / double-crédit Stripe** → idempotence `stripe_event_id` + ledger.
- **R2 : dérives de coût compute** → règles simples V1 + limites par plan.
- **R3 : exposition ComfyUI** → accès via portail / tunnel sécurisé + auth.
- **R4 : sécurité admin** → OAuth GitLab + allowlist emails/domains + RBAC strict.

---

## 19) Backlog (V1) — Epics / Stories / Acceptance Criteria

### EPIC A — RCC Wallet & Ledger
**A1. Ledger RCC (DB + API solde)**
- AC: `rcc_ledger` existe, `/me` calcule et retourne solde correct.

**A2. Débit RCC à la création job**
- AC: job non-admin crée une entrée ledger `JOB_RESERVE` (-1/-5) ; refus si solde insuffisant.

**A3. Remboursement si job échoue**
- AC: job failed crée `JOB_RELEASE` (+1/+5) ; solde restitué.

**A4. Historique RCC**
- AC: endpoint ou page affiche ledger filtrable.

### EPIC B — Jobs & intégration ComfyUI
**B1. Tracking jobs**
- AC: `jobs` stocke statuts + output + durée.

**B2. Typage Image/Vidéo**
- AC: mapping 1/5 RCC stable, testable.

### EPIC C — Stripe Hybride
**C1. Top-up checkout + webhook**
- AC: `TOPUP_GRANT` crédite RCC via webhook, idempotent.

**C2. Subscription checkout + renouvellement**
- AC: `SUBSCRIPTION_GRANT` crédit mensuel via event, idempotent.

### EPIC D — Admin GitLab + Dashboard
**D1. GitLab OAuth**
- AC: login + callback ; admin-only routes protégées.

**D2. Admin dashboard KPIs**
- AC: stats & listes opérationnelles.

**D3. Gestion utilisateurs**
- AC: ajustement RCC (MANUAL_ADJUST) + toggle is_admin.

**D4. Gestion modèles + Ops ComfyUI**
- AC: install/list/delete + start/stop/status.

---

## 20) Plan de livraison (V1)

### Sprint 1 — Fondations RCC + Jobs
- schéma DB (users/jobs/ledger/logs)
- endpoints `/me`, `/jobs` (création + débit + blocage)
- tracking job + output

### Sprint 2 — Paiement hybride
- checkout top-up + webhook
- subscription + crédit mensuel + webhook
- idempotence + audit payments

### Sprint 3 — Admin + Ops + modèles
- GitLab OAuth + allowlist
- dashboard + users + jobs + logs
- start/stop/status + modèles install/list/delete

---

## 21) Roadmap V2 (indispensable : solde organisation)
> **V2 — Organisation Wallet**
- Ajouter `organizations` et `org_members`
- Ajouter `org_rcc_ledger` ou ledger unique avec `scope=USER|ORG`
- Autoriser un job à consommer RCC :
  - du user wallet (par défaut)
  - ou du org wallet (si “workspace” sélectionné)
- Gouvernance : rôles org (owner/admin/member), limites, budgets.

> **V2 — Modulation coûts**
- Ajuster coût RCC selon résolution/steps/durée vidéo (option).

---

## 22) Open Questions (à suivre mais non bloquantes pour POC)
1) Auth user : Supabase Auth direct ou auth interne (JWT) ?
2) Politique d’expiration des RCC (si besoin) ?
3) Limites par plan (concurrence jobs, résolutions, durée vidéo) ?
4) Stockage outputs : local volume vs object storage ?

---

## 23) Annexes (POC setup — rappel)
- ComfyUI en Docker (GPU) avec volumes `models/outputs/workflows`
- FastAPI portal + Supabase (ou SQLite fallback)
- Stripe : webhooks en local via Stripe CLI (POC)
- GitLab OAuth : app + redirect URI vers `/auth/gitlab/callback`

---

# Fin du PRD V1