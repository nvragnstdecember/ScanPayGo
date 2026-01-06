import qrcode

# 🔴 CHANGE THIS to your real IP address from ipconfig
url = "http://172.20.10.3:5000"

img = qrcode.make(url)
img.save("scanpaygo_mobile_qr.png")

print("Saved QR as scanpaygo_mobile_qr.png")