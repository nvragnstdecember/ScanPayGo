document.addEventListener("DOMContentLoaded", function () {
  const seatGrid = document.getElementById("seat-grid");
  const selectedSeatsInput = document.getElementById("selected_seats");
  const selectedSeatsDisplay = document.getElementById("selected-seats-display");
  const fareDisplay = document.getElementById("fare-display");

  if (seatGrid && selectedSeatsInput && selectedSeatsDisplay && fareDisplay) {
    const priceText = fareDisplay.textContent;
    let pricePerSeat = 0;
    const match = priceText.match(/₹([\d.]+)/);
    if (match) {
      pricePerSeat = parseFloat(match[1]);
    }

    seatGrid.addEventListener("click", function (e) {
      const btn = e.target.closest(".seat-btn");
      if (!btn || btn.classList.contains("seat-booked")) return;

      const seatNo = btn.getAttribute("data-seat");
      if (!seatNo) return;

      btn.classList.toggle("seat-selected");

      const selected = Array.from(
        seatGrid.querySelectorAll(".seat-btn.seat-selected")
      )
        .map((b) => b.getAttribute("data-seat"))
        .sort((a, b) => parseInt(a) - parseInt(b));

      if (selected.length === 0) {
        selectedSeatsDisplay.textContent = "None";
        fareDisplay.textContent = `₹${pricePerSeat.toFixed(2)} x 0 = ₹0.00`;
      } else {
        selectedSeatsDisplay.textContent = selected.join(", ");
        const total = selected.length * pricePerSeat;
        fareDisplay.textContent = `₹${pricePerSeat.toFixed(2)} x ${
          selected.length
        } = ₹${total.toFixed(2)}`;
      }

      selectedSeatsInput.value = selected.join(",");
    });
  }
});