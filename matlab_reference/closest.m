function varargout = closest(val,arr)

arr_diff = abs(arr - val);
[~,min_idx] = min(arr_diff);
arr_min = arr(min_idx);

varargout{1} = arr_min;
varargout{2} = min_idx;

end