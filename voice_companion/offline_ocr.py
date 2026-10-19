"""CPU neural OCR with packaged local models. No runtime model downloads."""
from pathlib import Path
from functools import lru_cache
import threading
import hashlib

_lock=threading.Lock()
MAX_PIXELS=32_000_000
MODEL_HASHES={'PP-OCRv6_det_small.onnx':'090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f',
              'PP-OCRv6_rec_small.onnx':'6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884',
              'ch_ppocr_mobile_v2.0_cls_mobile.onnx':'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c'}


@lru_cache(maxsize=1)
def engine():
    import rapidocr
    from rapidocr import RapidOCR
    models=Path(rapidocr.__file__).parent/'models'
    files={'Det.model_path':models/'PP-OCRv6_det_small.onnx',
           'Rec.model_path':models/'PP-OCRv6_rec_small.onnx',
           'Cls.model_path':models/'ch_ppocr_mobile_v2.0_cls_mobile.onnx'}
    if not all(p.is_file() for p in files.values()):
        raise OSError('The offline OCR models are missing. Reinstall the complete Voice Companion build.')
    for path in files.values():
        with path.open('rb') as model_file:digest=hashlib.file_digest(model_file,'sha256').hexdigest()
        if digest!=MODEL_HASHES[path.name]:
            raise OSError('An offline OCR model is damaged. Reinstall the complete Voice Companion build.')
    return RapidOCR(params={**{key:str(value) for key,value in files.items()},
                            'EngineConfig.onnxruntime.intra_op_num_threads':2,
                            'EngineConfig.onnxruntime.inter_op_num_threads':1,
                            'Global.log_level':'error','Global.text_score':0.0,
                            'Global.max_side_len':3500})


def recognize_pdf_page(path,index):
    import pypdfium2 as pdfium
    import numpy as np
    with pdfium.PdfDocument(str(path)) as document:
        page=document[index]
        try:
            width,height=page.get_size()
            scale=min(300/72,(MAX_PIXELS/max(1,width*height))**.5)
            bitmap=page.render(scale=scale)
            try:image=np.array(bitmap.to_pil().convert('RGB'))
            finally:bitmap.close()
        finally:page.close()
    with _lock:result=engine()(image)
    if not result.txts:return '',['Page '+str(index+1)+': OCR found no text.']
    lines=list(zip(result.txts,result.scores))
    low=sum(score<.85 for _,score in lines)
    warnings=['Page '+str(index+1)+': offline OCR text; original formatting is inferred from the scanned image.']
    if low:warnings.append(str(low)+' OCR lines on page '+str(index+1)+' have low confidence. Check against the original image.')
    return '\n'.join(text for text,_ in lines),warnings


if __name__=='__main__':
    engine()
    print('Verified local offline OCR models load successfully.')
