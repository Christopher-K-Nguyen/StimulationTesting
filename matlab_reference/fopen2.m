function fopen2(fid)

status = fid.Status;
if contains(status,'closed')
    fopen(fid);
end

end