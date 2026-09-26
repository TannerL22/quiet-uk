"""Independent display alpha checks on the shared Mercator lattice."""
import numpy as np
from PIL import Image
from rasterio.warp import transform


def check(release):
    records = {(r['tile'],r['source'],r['metric']):r for r in release.records.values() if r['site']=='canary'}
    expected_masks = {}
    def pixels(record):
        with Image.open(release.verified_path(record['display']['path'])) as image:
            alpha=np.array(image)[:,:,3]>0
        lon,lat=record['display']['corners'][0]
        x,y=transform('EPSG:4326','EPSG:3857',[lon],[lat])
        return alpha,round(x[0]/10),round(-y[0]/10)
    pairs=pixel_count=0
    for (tile,source,metric),left in records.items():
        row,col=map(int,tile[1:].split('c'))
        for other in (f'r{row+1}c{col}',f'r{row}c{col+1}'):
            right=records.get((other,source,metric))
            if right is None: continue
            a,ax,ay=pixels(left);b,bx,by=pixels(right)
            x,y=max(ax,bx),max(ay,by)
            endx,endy=min(ax+a.shape[1],bx+b.shape[1]),min(ay+a.shape[0],by+b.shape[0])
            aa=a[y-ay:endy-ay,x-ax:endx-ax];bb=b[y-by:endy-by,x-bx:endx-bx]
            if np.any(aa & bb): raise ValueError(f'Double opacity at {tile}/{other}/{source}/{metric}')
            bounds=left['core_bounds'];other_bounds=right['core_bounds']
            w,s,e,n=min(bounds[0],other_bounds[0]),min(bounds[1],other_bounds[1]),max(bounds[2],other_bounds[2]),max(bounds[3],other_bounds[3])
            key=(x,y,endx,endy,w,s,e,n)
            if key not in expected_masks:
                expected=np.zeros(aa.shape,dtype=bool)
                for start in range(0,aa.size,65536):
                    stop=min(start+65536,aa.size)
                    indices=np.arange(start,stop)
                    xx=(x+indices%aa.shape[1]+.5)*10
                    yy=-(y+indices//aa.shape[1]+.5)*10
                    xs,ys=transform('EPSG:3857','EPSG:27700',xx,yy)
                    xs,ys=np.asarray(xs),np.asarray(ys)
                    expected.reshape(-1)[start:stop]=(xs>=w)&(xs<e)&(ys>s)&(ys<=n)
                expected_masks[key]=expected
            expected=expected_masks[key]
            if not np.array_equal(aa|bb,expected): raise ValueError(f'Display gap at {tile}/{other}/{source}/{metric}')
            pairs+=1;pixel_count+=aa.size
    return {'release_id':release.manifest['release_id'],'display_seam_pairs':pairs,
            'overlap_display_pixels_checked':pixel_count,'double_opacity_pixels':0,'gap_pixels':0}


