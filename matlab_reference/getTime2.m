function [File,time] = getTime2(File)
%% Variables
isStruct = isstruct(File);
isVISA = strcmpi(class(File),'visa');
if isStruct
    oscilloscope = File.Oscilloscope.Object;      % oscilloscope
elseif isVISA
    oscilloscope = File;
end
digitalDelay = File.Stimulator.DigitalDelay * 1e-6;

time = File.Oscilloscope.Settings.Time;
hasTime = ~isempty(time);

%% Function
startTime = tic;
if ~hasTime
    % Settings
    File = getSettings(File,'time');
    fprintf('\t');
    time = [];
else
    time = File.Oscilloscope.Settings.Time;
end

fprintf('Capturing time...');
if ~hasTime
    fprintf(oscilloscope,'DATa:SOUrce CH1');
    while isempty(time) || ~(any(time < 0 ) && any(time > 0))
        % Get constant to convert digital to analog
        % time of first data point
        XZEro = File.Oscilloscope.Settings.HorizontalStart;  % get time of first data point
        while isnan(XZEro) || abs(XZEro) > 0.1
            fprintf(oscilloscope,'WFMPre:XZEro?');        	% query time of first data point
            XZEro_raw = fgetl(oscilloscope);
            XZEro = str2double(XZEro_raw);	 %#ok<*ST2NM> % get time of first data point
            File.Oscilloscope.Settings.HorizontalStart = XZEro;
        end

        % horizontal interval number
        XINcr = File.Oscilloscope.Settings.Interval;         % get horizontal interval number
        while XINcr == 0 || isnan(XINcr) || XINcr == XZEro
            fprintf(oscilloscope,'WFMPre:XINcr?');        % query horitzontal sampling interval
            XINcr_raw = fgetl(oscilloscope);
            XINcr = str2double(XINcr_raw);	% get horizontal interval number
            File.Oscilloscope.Settings.Interval = XINcr;
        end

        % number of points
        NR_Pt = File.Oscilloscope.Settings.DataLength;	    % get query number of points in CURve
        count = 0;
        while NR_Pt < 100
            count = count + 1;
            fprintf(oscilloscope,'WFMPre:NR_Pt?');         % query number of points in CURve
            NR_Pt_raw = fgetl(oscilloscope);
            NR_Pt = str2double(NR_Pt_raw);	% get query number of points in CURve
            if count > 10
                NR_Pt = 2500;
            end
            File.Oscilloscope.Settings.DataLength = NR_Pt;
        end

        % PT_OFf = Settings.TriggerOffset;   % get horizontal offset number

        % Acquire time
        idx_arr = 1:NR_Pt;
        XUNits = (idx_arr - 1) * XINcr + XZEro;
        % timeEnd = XINcr * (NR_Pt - 1);
        % XUNits = linspace(0,timeEnd,NR_Pt) + XZEro;  % time vector
        time_raw = transpose(XUNits);
        time = time_raw - digitalDelay;
    end
    File.Oscilloscope.Settings.Time = time;
end

[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);
fprintf(oscilloscope,'ACQuire:STAte RUN');

end