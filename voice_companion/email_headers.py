"""Staged keyboard and voice editing of enabled email headers and their order."""
HEADERS=('from','subject','date','size')
class HeaderEditor:
    def __init__(self,session):
        self.session=session
        self.enabled=[x.strip() for x in session.values['email_header_order'].split(',')]
        self.order=self.enabled+[x for x in HEADERS if x not in self.enabled]
        self.index=0
    def label(self,index=None):
        index=self.index if index is None else index
        name=self.order[index]
        return name.capitalize()+', '+('enabled' if name in self.enabled else 'disabled')+', '+str(index+1)+' of 4'
    def save(self):
        self.session.set('email_header_order',', '.join(x for x in self.order if x in self.enabled))
    def toggle(self):
        name=self.order[self.index]
        if name in self.enabled:
            if len(self.enabled)==1:raise ValueError('Keep at least one spoken header enabled.')
            self.enabled.remove(name)
        else:self.enabled.append(name)
        self.save();return self.label()
    def move(self,delta):
        target=max(0,min(len(self.order)-1,self.index+delta))
        self.order[self.index],self.order[target]=self.order[target],self.order[self.index]
        self.index=target;self.save();return self.label()
    def select(self,delta):
        self.index=max(0,min(3,self.index+delta));return self.label()
