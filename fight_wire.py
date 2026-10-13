"""CRC-protected state snapshots alongside the existing KSLT1 input packets."""
try:
    import ujson as json
except ImportError:
    import json
try:
    crc32
except NameError:
    from protocol import crc32, decode, PREFIX, struct, binascii

POSES=('Standing','HitRight','HitLeft','Blocking','LeadRight','LeadLeft')

def state_line(session, kind, data):
    # The live game state fits in a short binary packet, safely hex-wrapped.
    if kind=='state' and len(data)==3 and len(data[1])==7:
        number,state,waiting=data
        frame,hp,x,pose,flash,parry,over=state
        body=struct.pack('<III8B2H2B',session,number,frame,
                         hp[0],hp[1],x[0],x[1],POSES.index(pose[0]),POSES.index(pose[1]),
                         flash[0],flash[1],parry[0],parry[1],int(over),int(waiting))
        return b'FIGHT2:'+binascii.hexlify(body+struct.pack('<I',crc32(body)))+b'\n'
    body=json.dumps([session,kind,data]).encode('ascii')
    return b'FIGHT1:'+body+b'|'+('%08x'%crc32(body)).encode('ascii')+b'\n'

class FightParser:
    def __init__(self):
        self.buffer=bytearray(); self.dropping=False; self.errors=0
    def feed(self,data):
        messages=[]
        for value in data:
            if value==10:
                if not self.dropping:
                    try:
                        line=bytes(self.buffer).rstrip(b'\r')
                        if line.startswith(b'FIGHT2:'):
                            raw=binascii.unhexlify(line[7:])
                            if len(raw)!=30 or crc32(raw[:-4])!=struct.unpack('<I',raw[-4:])[0]:raise ValueError('CRC/length')
                            v=struct.unpack('<III8B2H2B',raw[:-4])
                            if v[7]>5 or v[8]>5 or v[3]>100 or v[4]>100 or v[5]>63 or v[6]>63:raise ValueError('state range')
                            state=[v[2],[v[3],v[4]],[v[5],v[6]],[POSES[v[7]],POSES[v[8]]],
                                   [v[9],v[10]],[v[11],v[12]],bool(v[13])]
                            messages.append(('state',[v[0],'state',[v[1],state,bool(v[14])]]))
                        elif line.startswith(b'FIGHT1:'):
                            body,checksum=line[7:].rsplit(b'|',1)
                            if len(checksum)!=8 or crc32(body)!=int(checksum,16): raise ValueError('CRC')
                            packet=json.loads(body)
                            if not isinstance(packet,list) or len(packet)!=3 or packet[1] not in ('state','ack'): raise ValueError('state fields')
                            messages.append(('state',packet))
                        else:
                            messages.append(('input',decode(line+b'\n')))
                    except (ValueError,TypeError): self.errors+=1
                self.buffer=bytearray(); self.dropping=False
            elif not self.dropping:
                if len(self.buffer)>=1024:
                    self.buffer=bytearray(); self.dropping=True; self.errors+=1
                else: self.buffer.append(value)
        return messages
