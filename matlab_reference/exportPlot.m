function exportPlot(obj,filepath,varargin)

startTime = tic;
if isfile(filepath)
    delete(filepath);
end
[~,filename,ext] = fileparts(filepath);
fprintf('Exporting "%s%s"...',filename,ext);
if ~isempty(varargin)
    resolution = varargin{1};
else
    resolution = 600;
end
objClass = class(obj);
if contains2(objClass,'Figure')
    resolution_use = sprintf('-r%d',resolution);
    print(obj,filepath,'-dtiff',resolution_use);
else
    exportgraphics(obj,filepath, ...
        'Resolution',resolution, ...
        'BackgroundColor','none');
end
% if contains2(objClass,'Figure')
%     obj.Color = 'w';
% end
% isDone = false;
% saveTime = tic;
% checkTime = 300;
% increment = 300;
% while ~isDone
%     try
%         exportgraphics(obj,filepath, ...
%             'Resolution',resolution, ...
%             'BackgroundColor','none');
%     %     isDone = true;
%     % catch
%     %     if toc(saveTime) > checkTime
%     %         checkTime = checkTime + increment;
%     %         resolution = resolution - 100;
%     %     end
%     %     if resolution < 100
%     %         resolution = 600;
%     %     end
%     % end
% % end

[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end