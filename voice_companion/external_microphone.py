"""Fail-closed external microphone detection, independent of keyboard use."""
import json
import os
import re
import subprocess
import threading

PNP_SCRIPT=r'''
$ErrorActionPreference='Stop'
$media=@(Get-PnpDevice -PresentOnly -Class MEDIA -ErrorAction SilentlyContinue)
$result=@(foreach($endpoint in @(Get-PnpDevice -PresentOnly -Class AudioEndpoint)) {
  $container=(Get-PnpDeviceProperty -InstanceId $endpoint.InstanceId -KeyName 'DEVPKEY_Device_ContainerId' -ErrorAction SilentlyContinue).Data
  foreach($device in $media) {
    $other=(Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName 'DEVPKEY_Device_ContainerId' -ErrorAction SilentlyContinue).Data
    if($container -and $other -and $container -eq $other) {
      $id=$device.InstanceId
      for($depth=0;$depth -lt 6 -and $id;$depth++) {
        if($id -match '^(USB|BTH|BTHENUM|BTHLEDEVICE)\\') {
          [pscustomobject]@{name=$endpoint.FriendlyName;connection=$id};break
        }
        $id=(Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
      }
    }
  }
})
ConvertTo-Json -InputObject $result -Compress
'''

def connected_external_endpoints():
    if os.name!='nt':return []
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',PNP_SCRIPT],capture_output=True,text=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW,check=True)
        values=json.loads(result.stdout.lstrip('\ufeff') or '[]')
        return values if isinstance(values,list) else [values]
    except (OSError,ValueError,subprocess.SubprocessError):return []

def external_input(device,endpoints=()):
    name=str(device.get('name',''));fold=name.casefold()
    if device.get('max_input_channels',0)<=0:return False
    if re.search(r'loopback|stereo mix|virtual|vb-audio|voicemeeter|internal|built.?in|microphone array',fold):return False
    # Require a present Windows endpoint associated with USB/Bluetooth hardware.
    # A friendly name alone cannot prove that a microphone is external.
    return any((str(e.get('name','')).casefold()==fold or len(fold)>=25 and str(e.get('name','')).casefold().startswith(fold)) and re.match(r'^(USB|BTH)',str(e.get('connection','')),re.I) for e in endpoints)

def find_external_input(sd,endpoints=None):
    endpoints=connected_external_endpoints() if endpoints is None else endpoints
    devices=sd.query_devices()
    candidates=[(i,d) for i,d in enumerate(devices) if external_input(d,endpoints)]
    def priority(value):
        try:
            host=sd.query_hostapis(value[1]['hostapi'])
            return ('wasapi' not in host['name'].casefold(),value[0]!=host.get('default_input_device'))
        except (KeyError,TypeError,AttributeError):return (True,True)
    return min(candidates,key=priority) if candidates else None

class ExternalMicrophoneGuard:
    def __init__(self,sd,index,device):
        self.sd=sd;self.index=index;self.name=device['name'];self.allowed=True
        self.stop_event=threading.Event()
    def check_connection(self):
        try:
            device=self.sd.query_devices(self.index)
            endpoints=connected_external_endpoints()
            self.allowed=device.get('name')==self.name and external_input(device,endpoints)
        except Exception:self.allowed=False
        return self.allowed
    def start(self):
        def monitor():
            while not self.stop_event.wait(3):
                self.check_connection()
        threading.Thread(target=monitor,name='External microphone connection',daemon=True).start()
        return self
    def close(self):self.allowed=False;self.stop_event.set()
