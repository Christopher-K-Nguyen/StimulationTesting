function isFineScale = setFineScale(oscilloscope,scopeChannel,yMax_mag)
if ~contains2(scopeChannel,'MATH')
    % Adjust fine scaling
    scale = yMax_mag / 4;
    scale_char = sprintf('%e',scale);
    scale_len = length(scale_char);
    e_idx = strfind(scale_char,'e');
    factor_char = scale_char(1:e_idx-1);
    factor_num = str2double(factor_char);
    scale_factor = (ceil(factor_num * 10) + 0.2) / 10;
    pow_char = scale_char(e_idx:scale_len);
    scale_char = sprintf('%.2f%s',scale_factor,pow_char);
    scale_new = str2double(scale_char);

    % Check
    scale_check = [];
    scale_use = sprintf('%s:SCAle %s',scopeChannel,scale_char);
    scale_query = sprintf('%s:SCAle?',scopeChannel);
    while ~isequal(scale_check,scale_new)
        fprintf(oscilloscope,scale_use);
        fprintf(oscilloscope,scale_query);
        scale_raw = fgetl2(oscilloscope);
        scale_check = str2double(scale_raw);
    end
end
isFineScale = true;

end