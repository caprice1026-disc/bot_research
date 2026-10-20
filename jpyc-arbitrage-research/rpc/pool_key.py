# Ethereum Keccak-256, implemented locally; no external package installation.
R=[0,1,62,28,27,36,44,6,55,20,3,10,43,25,39,41,45,15,21,8,18,2,61,56,14]
RC=[0x1,0x8082,0x800000000000808a,0x8000000080008000,0x808b,0x80000001,0x8000000080008081,0x8000000000008009,0x8a,0x88,0x80008009,0x8000000a,0x8000808b,0x800000000000008b,0x8000000000008089,0x8000000000008003,0x8000000000008002,0x8000000000000080,0x800a,0x800000008000000a,0x8000000080008081,0x8000000000008080,0x80000001,0x8000000080008008]
M=(1<<64)-1
def rol(v,n):return ((v<<n)|(v>>(64-n)))&M if n else v
def keccak(data):
 data=bytearray(data);data.append(1)
 while len(data)%136!=135:data.append(0)
 data.append(128);a=[0]*25
 for off in range(0,len(data),136):
  for i in range(17):a[i]^=int.from_bytes(data[off+8*i:off+8*i+8],'little')
  for rc in RC:
   c=[a[x]^a[x+5]^a[x+10]^a[x+15]^a[x+20] for x in range(5)]
   d=[c[(x-1)%5]^rol(c[(x+1)%5],1) for x in range(5)]
   for i in range(25):a[i]^=d[i%5]
   b=[0]*25
   for y in range(5):
    for x in range(5):b[y+5*((2*x+3*y)%5)]=rol(a[x+5*y],R[x+5*y])
   for y in range(5):
    for x in range(5):a[x+5*y]=b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y])
   a[0]^=rc
 return b''.join(x.to_bytes(8,'little') for x in a)[:32]
def word(x):return int(x,16).to_bytes(32,'big') if isinstance(x,str) else x.to_bytes(32,'big')
assert keccak(b'').hex()=='c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'
a='0x3c499c542cef5e3811e1192ce70d8cc03d5c3359';b='0xe7c3d8c9a439fede00d2600032d5db0be71c3c29'
if __name__=='__main__':
 for spacing in [1,10,60,200]:
  encoded=b''.join(word(x) for x in [a,b,500,spacing,0]);print(spacing,'0x'+keccak(encoded).hex())
 for sig in ['getSlot0(bytes32)','getLiquidity(bytes32)','quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))']: print(sig,keccak(sig.encode()).hex()[:8])
