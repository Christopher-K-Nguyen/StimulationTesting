function YUNits_monitor = getCurve(File,scopeChannel)
%% Variables
scope = File.Oscilloscope.Object;
% Initialization
CURVe = zeros(DATA_LEN,1);
YUNits_monitor =  zeros(DATA_LEN,1);
YUNits_scaled =  zeros(DATA_LEN,1);
YUNits =  zeros(DATA_LEN,1);
YZEro = 0;
YMULt = 0;
YOFf = 0;

%% Function
switch upper(scopeChannel)
    case 'CH1'
        fprintf('Capturing voltage...');
    case 'CH2'
        fprintf('Capturing current...');
end

source_use = sprintf('DATa:SOUrce %s',scopeChannel);
fprintf(scope,source_use);  	% query channel
fprintf(scope,'CURVe?');        % query waveform data
CURVe = str2num(fscanf(scope)); %#ok<*ST2NM> % get numeric waveform data

% Get constants to convert digital to analog
fprintf('processing...');

% Conversion factor
fprintf(scope,'WFMPre:YZEro?');	% query conversion factor
YZEro = str2num(fscanf(scope)); % get conversion factor number

% Vertical scale factor
fprintf(scope,'WFMPre:YMUlt?');	% query vertical scale factor
YMULt = str2num(fscanf(scope)); % get vertical scale factor number

% Vertical position
fprintf(scope,'WFMPre:YOFf?');	% query vertical position
YOFf = str2num(fscanf(scope));  % get vertical position number

% Convert digitized waveform
YUNits_monitor = YZEro + YMULt * (CURVe - YOFf);    % vertical units

fprintf('OK\n');

end