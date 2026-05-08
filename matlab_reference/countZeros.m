function numOfZeros = countZeros(num,direction)
format long;

num_char = num2str(num,'%g');
num_len = length(num_char);
point_idx = strfind(num_char,'.');
switch lower(direction)
    case {'left','l'}
        if ~isempty(point_idx)
            idx_arr = flip(1:point_idx-1);
        else
            idx_arr = flip(1:num_len);
        end
    case {'right','r'}
        idx_arr = point_idx+1:num_len;
    otherwise
        numOfZeros = [];
        return;
end

numOfZeros = 0;
for idx = idx_arr
    if contains2(num_char(idx),'0')
        numOfZeros = numOfZeros + 1;
    else
        break;
    end
end

end