import numpy as np, wave
SR=44100; DUR=20.0; N=int(SR*DUR); rng=np.random.default_rng(7)
mus=np.zeros((N,2)); sfx=np.zeros((N,2))
def hz(m): return 440*2**((m-69)/12)
def add(buf,t,sig,pan=0.0,g=1.0):
    i=int(t*SR); n=min(len(sig),N-i)
    if n<=0: return
    l=np.cos((pan+1)*np.pi/4); r=np.sin((pan+1)*np.pi/4)
    buf[i:i+n,0]+=sig[:n]*g*l*1.414; buf[i:i+n,1]+=sig[:n]*g*r*1.414
def env(n,a,d):
    t=np.arange(n)/SR; e=np.minimum(1,t/max(a,1e-4))*np.exp(-t/d); return e
def lp(x,a):  # one-pole lowpass
    y=np.empty_like(x); s=0.0
    for i,v in enumerate(x): s+=a*(v-s); y[i]=s
    return y
def kick(g=1):
    n=int(.35*SR); t=np.arange(n)/SR; f=48+90*np.exp(-t*30)
    return np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*9)*g
def hat(d=.04):
    n=int(.08*SR); x=rng.standard_normal(n); x=x-lp(x,.5); return x*env(n,.001,d)
def clap():
    n=int(.25*SR); x=rng.standard_normal(n); x=lp(x,.35)-lp(x,.08); e=env(n,.002,.07)
    for k in (0.011,0.022): e+=np.roll(env(n,.001,.01),int(k*SR))*.6
    return x*e
def pluck(m,dec=.35,bright=1.0):
    n=int(1.2*SR); t=np.arange(n)/SR; f=hz(m)
    x=(np.sin(2*np.pi*f*t)+.35*bright*np.sin(4*np.pi*f*t)+.12*bright*np.sin(6*np.pi*f*t))
    return x*env(n,.003,dec)
def pad(ms,dur):
    n=int(dur*SR); t=np.arange(n)/SR; x=np.zeros(n)
    for m in ms:
        for det in (-.08,.08):
            f=hz(m+det); x+=np.sign(np.sin(2*np.pi*f*t))*.5+np.sin(2*np.pi*f*t)
    x=lp(x,.03); a=np.minimum(1,t/.25)*np.minimum(1,(dur-t)/.4); return x*a/len(ms)
def bass(m,dur):
    n=int(dur*SR); t=np.arange(n)/SR; f=hz(m)
    x=np.sin(2*np.pi*f*t)+.3*np.sin(4*np.pi*f*t); return x*env(n,.005,dur*.8)*np.minimum(1,(dur-t)*20)
def noise_swell(dur):
    n=int(dur*SR); x=rng.standard_normal(n); t=np.arange(n)/SR
    out=np.zeros(n); s=0.0
    for i in range(n):
        a=.01+.25*(t[i]/dur)**2; s+=a*(x[i]-s); out[i]=s
    return out*(t/dur)**2
def boom():
    n=int(1.5*SR); t=np.arange(n)/SR; f=40+60*np.exp(-t*12)
    return np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*2.5)

B=.5  # 120 BPM
prog=[(57,[69,72,76]),(53,[69,72,77]),(48,[67,72,76]),(55,[67,71,74])]  # Am F C G
def section_on(t): return not (3.3<=t<6.0)
# drums
kicks=[]
for i in range(40):
    t=i*B
    if t>=19.0: break
    if 3.3<=t<5.9:   # half-time during reveal
        if i%4==0: add(mus,t,kick(.8)); kicks.append(t)
        continue
    if 16.9<=t<17.05: continue
    add(mus,t,kick(1)); kicks.append(t)
    if i%2==1: add(mus,t,clap(),0,.35)
    add(mus,t+B/2,hat(.03),.3,.12); add(mus,t,hat(.015),-.3,.06)
# bass + pads per bar
for bar in range(10):
    t0=bar*2.0; root,ch=prog[bar%4]
    if t0>=18: add(mus,t0,pad(ch,2.0),0,.18); continue
    add(mus,t0,pad(ch,2.0),0,.16 if t0<3 else .2)
    for k,(off,d) in enumerate([(0,.45),(.75,.2),(1.0,.45),(1.5,.4)]):
        if 3.3<=t0+off<6.0 and k: continue
        add(mus,t0+off,bass(root-12,d),0,.45)
# sidechain duck
duck=np.ones(N)
for t in kicks:
    i=int(t*SR); n=min(int(.25*SR),N-i); duck[i:i+n]=np.minimum(duck[i:i+n],1-.55*np.exp(-np.arange(n)/SR/.07))
mus[:,0]*=duck; mus[:,1]*=duck  # (kick also ducked lightly; fine)
# ---- SFX (in A minor pentatonic) ----
pent=[81,84,88,91,93]
for i,m in enumerate(pent): add(sfx,.25+i*.5,pluck(m,.28),(-.4+.2*i),.32)
add(sfx,2.3,noise_swell(1.0),0,.5)
add(sfx,3.3,boom(),0,.9); add(sfx,3.3,pad([57,64,69,72,76],2.6),0,.25)
for i in range(22):   # typing ticks, very soft
    add(sfx,6.25+i*1.5/22,hat(.006),.2,.05)
add(sfx,7.95,pluck(76,.12,.5),0,.25)
add(sfx,8.75,boom(),0,.55); add(sfx,8.75,pluck(57,.6,1.5),0,.35); add(sfx,8.75,pluck(64,.6,1.2),0,.2)
add(sfx,9.45,pluck(88,.18),.3,.22)
for i,m in enumerate([76,79,81,84,88]): add(sfx,11.15+i*.28,pluck(m,.2,.7),.3,.2)
add(sfx,16.6,noise_swell(.45),0,.35)
add(sfx,17.05,boom(),0,.8); add(sfx,17.15,pluck(81,1.2,.8),0,.25); add(sfx,17.15,pluck(69,1.2,.8),0,.2)
# reverb (shared space)
def reverb(x,dec=1.4,mix=.22):
    n=int(dec*SR); ir=rng.standard_normal((n,2))*np.exp(-np.arange(n)/SR/(dec/5))[:,None]; ir/=np.sqrt((ir**2).sum(0))
    L=len(x)+n; F=1<<(L-1).bit_length(); y=np.zeros_like(x)
    for c in range(2): y[:,c]=np.fft.irfft(np.fft.rfft(x[:,c],F)*np.fft.rfft(ir[:,c],F),F)[:len(x)]
    return x+mix*y
mix=reverb(mus,1.2,.15)*.9+reverb(sfx,1.6,.3)*.75
# fades + limiter
t=np.arange(N)/SR; mix*=np.minimum(1,t/.05)[:,None]*np.clip((DUR-t)/1.5,0,1)[:,None]
mix=np.tanh(mix/np.max(np.abs(mix))*1.15)*.85
w=wave.open('audio.wav','wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
w.writeframes((mix*32767).astype('<i2').tobytes()); w.close(); print('ok')
