/** Phase E -- hospital-side donation verification queue. */
import HospitalSubNav from "./HospitalSubNav";
import DonationReviewQueue from "../../components/DonationReviewQueue";

export default function DonationReviews() {
  return (
    <div className="min-h-screen bg-background">
      <HospitalSubNav title="Donation Verification" subtitle="Confirm donations donors linked to your facility" />
      <main className="max-w-4xl mx-auto px-4 md:px-margin-desktop py-6 md:py-8">
        <DonationReviewQueue />
      </main>
    </div>
  );
}
