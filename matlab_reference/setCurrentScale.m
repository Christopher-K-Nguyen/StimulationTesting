function setCurrentScale(File,amplitude1)
%% Variables
scope = File.Oscilloscope.Object;
currentStim = abs(amplitude1);
% phaseWidth = File.Parameters.PhaseWidth1;

%% Function
fprintf('Setting CH2 scale...');
startTime = tic;
scale = currentStim / 4 * 1e-3;
scale_char = sprintf('%e',scale);
scale_len = length(scale_char);
e_idx = strfind(scale_char,'e');
factor_char = scale_char(1:e_idx-1);
factor_num = str2double(factor_char);
if currentStim <= 5
    scale_factor = (ceil(factor_num * 10) + 60.2) / 10;
elseif currentStim <= 10
    scale_factor = (ceil(factor_num * 10) + 42.2) / 10;
elseif currentStim <= 20
    scale_factor = (ceil(factor_num * 10) + 32.2) / 10;
elseif currentStim <= 40
    scale_factor = (ceil(factor_num * 10) + 28.2) / 10;
elseif currentStim <= 60
    scale_factor = (ceil(factor_num * 10) + 24.2) / 10;
elseif currentStim <= 80
    scale_factor = (ceil(factor_num * 10) + 20.2) / 10;
else
    scale_factor = (ceil(factor_num * 10) + 10.2) / 10;
end
pow_char = scale_char(e_idx:scale_len);
scale_new = sprintf('%.3g%s',scale_factor,pow_char);
scale_use = sprintf('CH2:SCAle %s',scale_new);
fprintf(scope,scale_use);
% unitCH2 = getUnit(File,'CH2');
unitCH2 = 'V';
[endTime,unit] = getEndTime(startTime);
% fprintf('%s %s\n',scale_new,unit);
fprintf('%s %s (%.2f %s)\n',scale_new,unitCH2,endTime,unit);

end