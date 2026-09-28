from py_vapid import Vapid
import base64

v = Vapid()
v.generate_keys()

# Get raw keys
priv = v.private_key.private_numbers().private_value.to_bytes(32, 'big')
pub = v.public_key.public_numbers()

# Convert to URL-safe base64 (without padding)
priv_b64 = base64.urlsafe_b64encode(priv).rstrip(b'=').decode()
pub_bytes = b'\x04' + pub.x.to_bytes(32, 'big') + pub.y.to_bytes(32, 'big')
pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()

print("\n" + "="*60)
print("VAPID KEYS FOR WEB PUSH")
print("="*60)
print(f"\nPUBLIC KEY:\n{pub_b64}")
print(f"\nPRIVATE KEY:\n{priv_b64}")
print("\n" + "="*60)