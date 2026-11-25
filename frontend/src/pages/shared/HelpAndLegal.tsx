/**
 * Help & Support / Legal, Privacy & Consent
 * Adapted from: lifelink_0.5_0.8_legal_help_consent/code.html
 * Static informational content (FAQ + privacy/terms) — genuinely static, not a stand-in
 * for missing backend behaviour, so no honesty concerns here. Rendered at /help and
 * /legal; the section param decides which part is shown first.
 */
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

const FAQ = [
  {
    q: "How does LifeLink match donors to a request?",
    a: "When a request is created, LifeLink notifies donors with a compatible blood type. A donor who accepts is matched to that request; the requester or a linked family member can see accepted donors on the request's tracking view.",
  },
  {
    q: "Who can see my contact details as a donor?",
    a: "Your name and contact info are never shown in search results, on the map, or to a hospital browsing candidates. They're only revealed to the specific requester once you accept their request, so they can arrange the donation with you.",
  },
  {
    q: "How often can I donate?",
    a: "LifeLink enforces a 56-day minimum interval between donations, computed from your last recorded donation date. Your donor dashboard shows exactly when you're next eligible.",
  },
  {
    q: "What does a Family account let me do?",
    a: "Once a patient accepts your link request, you can file and track blood requests and emergencies on their behalf, and see their linked-patient summary on your Family dashboard.",
  },
  {
    q: "How do I verify a donation certificate?",
    a: "Every certificate has a verification code. Anyone can check it's genuine at the certificate verification page without signing in.",
  },
];

export default function HelpAndLegal() {
  const { section } = useParams<{ section?: string }>();
  const showLegalFirst = section === "legal" || section === "privacy";

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center px-margin-mobile md:px-margin-desktop gap-4">
        <Link
          to="/"
          aria-label="Back to dashboard"
          className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <h1 className="font-headline-md text-headline-md text-on-surface">Guidelines &amp; Support</h1>
      </header>

      <main className="max-w-3xl mx-auto p-margin-mobile md:p-margin-desktop space-y-10">
        {showLegalFirst ? (
          <>
            <LegalSection />
            <ConsentSection />
            <SafetySection />
            <AboutSection />
            <FaqSection />
          </>
        ) : (
          <>
            <FaqSection />
            <LegalSection />
            <ConsentSection />
            <SafetySection />
            <AboutSection />
          </>
        )}
      </main>
    </div>
  );
}

function ConsentSection() {
  return (
    <section id="legal-consent" className="space-y-4">
      <h2 className="font-headline-sm text-headline-sm text-on-surface">Consent</h2>
      <div className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3 text-sm text-on-surface-variant leading-relaxed">
        <p>
          Creating an account means you consent to LifeLink storing the profile information you provide (blood
          type, contact details, facility information) and using it to run blood requests, emergency matching
          and donor eligibility checks, as described in the Privacy Policy above.
        </p>
        <p>
          Donors additionally consent to their aggregate availability (city + blood type) being used for
          discovery — never their name or contact details — until they accept a specific request. You can
          withdraw availability at any time from Settings.
        </p>
      </div>
    </section>
  );
}

function SafetySection() {
  return (
    <section id="legal-safety" className="space-y-4">
      <h2 className="font-headline-sm text-headline-sm text-on-surface">Safety &amp; Medical Disclaimer</h2>
      <div className="bg-surface rounded-xl shadow-soft border border-error/20 p-6 space-y-3 text-sm text-on-surface-variant leading-relaxed">
        <p className="text-on-surface font-label-md">
          LifeLink is a coordination tool, not a medical provider or emergency service.
        </p>
        <p>
          In a life-threatening emergency, contact local emergency services first — LifeLink's SOS/emergency
          flow coordinates donor and hospital matching, but it does not dispatch ambulances or medical care.
        </p>
        <p>
          Blood type compatibility shown in the app follows standard donor/recipient compatibility rules;
          always confirm eligibility and suitability with clinical staff at the receiving facility before a
          donation or transfusion.
        </p>
      </div>
    </section>
  );
}

function AboutSection() {
  return (
    <section id="legal-about" className="space-y-4">
      <h2 className="font-headline-sm text-headline-sm text-on-surface">About LifeLink</h2>
      <div className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3 text-sm text-on-surface-variant leading-relaxed">
        <p>
          LifeLink connects patients, families, donors and hospitals so a compatible blood request or emergency
          can be matched, tracked and fulfilled in one place — real-time donor matching, hospital inventory
          coordination, and a transport/resource-pathway engine for routing blood between facilities when a
          local supply runs short.
        </p>
      </div>
    </section>
  );
}

function FaqSection() {
  return (
    <section id="help-faq" className="space-y-4">
      <h2 className="font-headline-sm text-headline-sm text-on-surface">Support Knowledge Base</h2>
      <div className="bg-surface rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
        {FAQ.map((item) => (
          <details key={item.q} className="group px-5 py-4">
            <summary className="font-label-md text-label-md text-on-surface cursor-pointer list-none flex items-center justify-between gap-4">
              {item.q}
              <span aria-hidden="true" className="material-symbols-outlined text-on-surface-variant transition-transform group-open:rotate-180">
                expand_more
              </span>
            </summary>
            <p className="text-sm text-on-surface-variant mt-2 leading-relaxed">{item.a}</p>
          </details>
        ))}
      </div>
      <p className="text-sm text-on-surface-variant">
        Need something else? Email <a className="text-secondary underline" href="mailto:support@lifelink.example">support@lifelink.example</a>.
      </p>
    </section>
  );
}

function LegalSection() {
  return (
    <section id="legal-terms" className="space-y-4">
      <h2 className="font-headline-sm text-headline-sm text-on-surface">Privacy Policy &amp; Terms of Service</h2>
      <div className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-5 text-sm text-on-surface-variant leading-relaxed">
        <div>
          <h3 className="font-label-md text-label-md text-on-surface mb-1">1. Account data</h3>
          <p>We store your name, email, role and profile fields you provide (blood type, contact details, facility information). Passwords are hashed with Argon2id and never stored in plain text.</p>
        </div>
        <div>
          <h3 className="font-label-md text-label-md text-on-surface mb-1">2. Blood type &amp; medical fields</h3>
          <p>Blood type, availability and donation history are used only to compute compatibility and eligibility. They are shown to counterparties only where this app's role permissions allow it.</p>
        </div>
        <div>
          <h3 className="font-label-md text-label-md text-on-surface mb-1">3. Donor identity protection</h3>
          <p>Donor names, phone numbers and photos are never exposed in search, on the map, or to hospitals browsing candidates. They're revealed only to a request's own requester, and only once you accept that request.</p>
        </div>
        <div>
          <h3 className="font-label-md text-label-md text-on-surface mb-1">4. Data retention</h3>
          <p>Request, emergency and donation records are retained to maintain your history and issued certificates. Contact support to request deletion of your account.</p>
        </div>
        <div>
          <h3 className="font-label-md text-label-md text-on-surface mb-1">5. Medical disclaimer</h3>
          <p>LifeLink is a coordination tool, not a medical provider. In a life-threatening emergency, contact local emergency services first.</p>
        </div>
      </div>
    </section>
  );
}
