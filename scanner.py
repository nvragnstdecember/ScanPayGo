import time
import cv2
import requests
import winsound
import re

API_BASE = "http://127.0.0.1:5000"  # Flask backend
WINDOW_NAME = "ScanPayGo - Bus Ticket Scanner"


def extract_ticket_code(decoded_text: str) -> str | None:
    """
    Extract ticket code from the QR content.
    We expect either:
      - full URL: http://127.0.0.1:5000/ticket/SPG-XXXXXXXX
      - or just the code: SPG-XXXXXXXX
    """
    m = re.search(r"(SPG-[A-Z0-9]+)", decoded_text)
    if m:
        return m.group(1)
    return None


def validate_ticket(ticket_code: str) -> dict:
    """
    MAIN LOGIC:

    1) Ask backend: /api/validate/<ticket_code>
       - if status=200 & valid -> already paid, just board.
       - if status=400 & reason='unpaid' -> we try wallet payment.

    2) Wallet payment: POST /api/wallet_pay/<ticket_code>
       - if 200 -> fare deducted from user's wallet, mark PAID.
       - if 400 -> insufficient wallet balance or error.
    """

    try:
        # STEP 1: Check current status
        url = f"{API_BASE}/api/validate/{ticket_code}"
        resp = requests.get(url, timeout=5)

        # ---- CASE A: Ticket already PAID ----
        if resp.status_code == 200:
            data = resp.json()
            if data.get("valid"):
                return {
                    "status": "valid",
                    "reason": "already_paid",
                    "data": data,
                }

        # ---- CASE B: Ticket exists but unpaid (400) ----
        if resp.status_code == 400:
            reason = resp.json().get("reason")

            if reason == "unpaid":
                # STEP 2: Try wallet deduction
                pay_url = f"{API_BASE}/api/wallet_pay/{ticket_code}"
                pay_resp = requests.post(pay_url, timeout=5)

                # Wallet payment success
                if pay_resp.status_code == 200:
                    pay_data = pay_resp.json()
                    return {
                        "status": "valid",
                        "reason": "wallet_paid",
                        "data": pay_data,
                    }

                # Wallet payment failed (not enough balance, etc.)
                else:
                    failure_reason = pay_resp.json().get("reason", "wallet_error")
                    return {
                        "status": "invalid",
                        "reason": failure_reason,
                        "data": None,
                    }

            # Some other failure reason from backend
            return {
                "status": "invalid",
                "reason": reason,
                "data": None,
            }

        # ---- CASE C: Ticket not found (404) ----
        if resp.status_code == 404:
            return {
                "status": "invalid",
                "reason": "Ticket Not Found",
                "data": None,
            }

        # Any other HTTP status
        return {
            "status": "invalid",
            "reason": f"http_{resp.status_code}",
            "data": None,
        }

    except Exception as e:
        print("API ERROR:", e)
        return {
            "status": "invalid",
            "reason": "api_error",
            "data": None,
        }


def beep_valid():
    winsound.Beep(1000, 200)
    winsound.Beep(1500, 150)


def beep_invalid():
    winsound.Beep(400, 300)


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open camera.")
        return

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 800, 600)

    detector = cv2.QRCodeDetector()

    last_code = None
    last_scan_time = 0
    overlay_until = 0
    overlay_color = (0, 0, 0)
    overlay_text = ""
    overlay_subtext = ""

    print("Scanner started. Press 'q' to quit.\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)

        data, points, _ = detector.detectAndDecode(frame)

        if points is not None and data:
            decoded_text = data
            pts = points[0].astype(int)

            # Draw polygon around QR
            for i in range(len(pts)):
                pt1 = tuple(pts[i])
                pt2 = tuple(pts[(i + 1) % len(pts)])
                cv2.line(frame, pt1, pt2, (255, 255, 0), 2)

            ticket_code = extract_ticket_code(decoded_text)
            if ticket_code:
                now = time.time()
                if ticket_code != last_code or now - last_scan_time > 3:
                    print(f"Scanned QR: {decoded_text} -> ticket_code={ticket_code}")
                    result = validate_ticket(ticket_code)
                    last_code = ticket_code
                    last_scan_time = now

                    # -------- SUCCESS CASES --------
                    if result["status"] == "valid":
                        if result["reason"] == "wallet_paid":
                            # Wallet payment just happened
                            pay_data = result["data"]
                            amount = pay_data.get("deducted_amount")
                            overlay_color = (0, 200, 0)  # green
                            overlay_text = "TICKET VALID FARE DEDUCTED"
                            overlay_subtext = f"Ticket {ticket_code} | Deducted Rs{amount:.2f}"
                            print("Wallet payment successful, fare deducted.")
                        else:
                            # already_paid
                            overlay_color = (0, 200, 0)
                            overlay_text = "TICKET VALID"
                            overlay_subtext = f"Ticket {ticket_code} (already paid)"
                            print("Ticket already paid, allowing boarding.")

                        beep_valid()

                    # -------- FAILURE CASES --------
                    else:
                        reason = result["reason"]
                        overlay_color = (0, 0, 255)  # red
                        overlay_text = "TICKET INVALID"
                        overlay_subtext = f"Reason: {reason}"
                        print("Ticket invalid:", reason)
                        beep_invalid()

                    overlay_until = now + 2.5

        # Overlay UI
        now2 = time.time()
        if now2 < overlay_until:
            overlay = frame.copy()
            alpha = 0.6
            cv2.rectangle(
                overlay, (0, 0), (frame.shape[1], frame.shape[0]), overlay_color, -1
            )
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

            cv2.putText(
                frame,
                overlay_text,
                (40, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (255, 255, 255),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                overlay_subtext,
                (40, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        cv2.imshow(WINDOW_NAME, frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()